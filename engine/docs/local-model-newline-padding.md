# Local-model newline padding (Gemma on LMStudio)

*Why some local Gemma models pad their output with `\n` until they hit `max_tokens` (truncating JSON-schema output into invalid JSON and spiralling stager retries), what the actual root cause is per runtime, and the layered fixes — engine-side net plus the per-runtime root fix.*

-----

## Symptom

A structured-output (`response_format: json_schema`) call to a local Gemma model on LMStudio returns a response whose `content` is a complete-looking JSON object **that never closes** — the closing `}` is replaced by hundreds/thousands of `\n` (or `\n  ` indent) characters padding the tail:

```jsonc
{
  "header": "...",
  "suggested_tags": ["data-source", "scope-management"]
  <newline + indent, repeated thousands of times, NO closing brace>
```

The response comes back with either `finish_reason: "length"` (padded all the way to `max_tokens`) **or** `finish_reason: "stop"` (padded until it happened to emit a recognized stop token). `completion_tokens` is huge (e.g. 18,000) for what should be a few-hundred-token object. Downstream, `_extract_json` can't recover the object (it never balanced), so the stager gets `parsed=None` and retries — a hot loop.

**This is a model/runtime defect, not a Laya bug.** Qwen (`<|im_end|>`), Llama (`<|eot_id|>`), etc. don't do this because their turn terminators are correctly registered as stop tokens.

-----

## Root cause: Gemma's turn terminator isn't in the runtime's stop set

Gemma is trained to end every assistant turn with **`<end_of_turn>` (token id 106)** — that's what the chat template emits. But Gemma's *nominal* `eos_token` is **`<eos>` (id 1)**, a different token it rarely emits mid-conversation. If the runtime's effective stop set contains only `<eos>` and **not** `<end_of_turn>`, then:

1. The model emits `<end_of_turn>` (106) to end its turn.
2. The runtime doesn't recognize 106 as a stop, and (being a special token) strips it from the text stream — so you never even see `<end_of_turn>` in `content`.
3. Generation continues past the intended end. The model is now **out-of-distribution**; the lowest-entropy continuation is filler — a newline / indent. It loops on `\n  \n  …`.
4. It stops only when it hits a token that *is* in the stop set: it either finally samples `<eos>` (1) → `finish_reason: "stop"`, or runs out of budget → `finish_reason: "length"`.

Under a `json_schema` grammar this produces *invalid* JSON specifically because the model drops into the padding loop **before** emitting the closing `}` — the grammar permits insignificant whitespace between tokens, so the sampler keeps picking whitespace instead of advancing to the closer.

The mechanism by which `<end_of_turn>` ends up outside the stop set differs by runtime:

### GGUF (llama.cpp engine)

In GGUF conversions, `<end_of_turn>` is frequently flagged token-type **`NORMAL`** instead of **`CONTROL`**. llama.cpp only auto-stops on tokens flagged as stop/EOS, so a `NORMAL`-flagged `<end_of_turn>` is treated as ordinary text and never stops the turn. (Upstream: llama.cpp #12433 / #22396, unsloth #5070 / #5386, HF transformers #32110 / #38182.)

### MLX (Apple-silicon engine)

MLX models carry no GGUF token-type flags — the stop set comes from the model's HuggingFace-style config. Gemma 3n (`model_type: gemma4`) is a **multimodal composite** model, and its config declares EOS in **three places that disagree**:

| Source | `eos_token_id` | Has `<end_of_turn>` (106)? |
|---|---|---|
| `generation_config.json` | `[1, 106, 50]` | ✅ |
| `config.json` (top-level) | `[1, 106, 50]` | ✅ |
| `config.json` → `text_config.eos_token_id` | `1` | ❌ **only `<eos>`** |
| `tokenizer_config.json` → `eos_token` | `"<eos>"` (id 1) | ❌ |

The MLX runtime builds the language model from **`text_config`** and derives its stop token from `text_config.eos_token_id` / the tokenizer's `eos_token` — **not** from `generation_config.json`. Both of those resolve to *only* `<eos>` (1). Proof the runtime ignores `generation_config.json`: that file already lists 106, yet the model still pads. So the effective stop set is `{1}`, and the mechanism above plays out exactly.

-----

## Fixes

Two layers. The engine-side net catches *any* misbehaving local model automatically; the per-runtime root fix stops the padding at the source for models you control.

### Layer 1 — Engine-side net (automatic, in `engine/laya/llm/client.py`)

Applied to any custom/local provider (`custom is not None`); cloud providers manage their own stops and are untouched.

- **Stop sequences** — `_LOCAL_STOP_SEQUENCES = ["<end_of_turn>", "<eos>", "<|im_end|>", "<|eot_id|>"]` is sent as `kwargs["stop"]` on every custom-provider call. Catches the variant where the terminator is emitted as *visible text* (e.g. a `NORMAL`-flagged GGUF token). **Does not help** when the terminator is a stripped special token (the MLX case) — there's no text to match.
- **Windowed repetition penalty** — `_LOCAL_REPEAT_PENALTY = 1.15` via `extra_body.repeat_penalty` for custom **+ schema** calls, to nudge the sampler off the `\n` loop toward the structural closer. `repeat_penalty` is a non-standard llama.cpp param; some servers (incl. LMStudio's OpenAI-compat endpoint, and MLX) may ignore it — the salvage below is the real backstop.
- **`_extract_json(content, allow_completion=…)`** — best-effort parse. The balanced-brace scan recovers a *complete* object followed by trailing padding/junk. When `allow_completion` is set (only at the final parse site, and only for a custom provider whose output matches `_looks_like_padding`), a last-resort pass (`_complete_json`) **rebuilds an object the model left unterminated by padding** — it strips the trailing whitespace (and a dangling comma) and appends the brackets needed to balance, with `json.loads` as the final guard. It bails if the padding-stripped body ends *inside a string* (that's a genuine mid-content truncation, handled by the doubling retry instead). This is what recovers the `finish_reason: "stop"` padding case, where nothing marks the response truncated so no retry ever fires.
- **`_looks_like_padding(content)`** — true when the tail is whitespace-/single-char-dominated. Gates both the completion salvage and the "skip the doubling retry" decision (doubling a padder just generates more padding).

Tests: `engine/tests/test_llm_client.py` (`_extract_json` / `_complete_json` salvage, stop-sequence injection, truncation handling).

### Layer 2 — Per-runtime root fix (stops the padding at the source)

**GGUF:**
- Update the **LM Runtime** (llama.cpp engine) in LMStudio — the token-type fix for Gemma has landed upstream; this is the highest-leverage single action.
- Use a correctly-converted GGUF (`<end_of_turn>` flagged `CONTROL`), or load the `google_gemma_instruct` LMStudio preset which registers the proper stop token.
- Adding `<end_of_turn>` to LMStudio's **Stop Strings** field works *only* if the broken GGUF renders it as text (the `NORMAL` case); it can't catch a stripped special token.

**MLX:** edit the field the runtime actually reads — `text_config.eos_token_id` — to include `<end_of_turn>`:

```jsonc
// config.json  →  text_config
"eos_token_id": [1, 106]   // was: 1
```

Then **fully eject and reload** the model in LMStudio (a soft reload won't re-read `config.json`). Confirm success by re-running a Gemma stage: you want `finish_reason: "stop"` with `completion_tokens` in the low hundreds (not thousands) and complete JSON. Note that Stop Strings do **not** help MLX here — 106 is a special token stripped before it reaches text, so the token-id stop set is the only lever.

Caveat: if LMStudio re-downloads / "repairs" the model, it overwrites the edit — re-apply, or keep a `config.json.bak`.

-----

## Confirmed case: `lmstudio-community/gemma-4-E4B-it-MLX-4bit` (Jul 2026)

Reproduced the padding on a stager call: `google/gemma-4-e4b` returned a well-formed object missing only its closing `}`, followed by ~17,800 `\n  ` padding tokens, `finish_reason: "stop"`, `completion_tokens: 18092`.

- Root cause: `config.json` → `text_config.eos_token_id` was `1` (only `<eos>`), while `generation_config.json` and the top-level config correctly listed `[1, 106, 50]`. The MLX runtime uses `text_config`, so `<end_of_turn>` (106) was never a stop.
- Fix: changed `text_config.eos_token_id` to `[1, 106]` (backup at `config.json.bak`), fully ejected + reloaded the model. **Padding gone** — clean, complete JSON with normal token counts.
- The engine-side completion salvage was added in the same pass and remains the net for other local models whose config can't be edited.
