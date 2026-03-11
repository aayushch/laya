# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for bundling the Laya engine into a single executable."""

import sys
from pathlib import Path

block_cipher = None

engine_dir = Path(SPECPATH)

a = Analysis(
    [str(engine_dir / 'laya' / 'main.py')],
    pathex=[str(engine_dir)],
    binaries=[],
    datas=[
        # Include migration SQL files
        (str(engine_dir / 'laya' / 'db' / 'migrations'), 'laya/db/migrations'),
        # Include prompt templates
        (str(engine_dir / 'laya' / 'llm' / 'prompts'), 'laya/llm/prompts'),
    ],
    hiddenimports=[
        # FastAPI / Starlette
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # aiosqlite
        'aiosqlite',
        # structlog
        'structlog',
        'structlog.dev',
        # LiteLLM / OpenAI
        'litellm',
        'openai',
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        # Sentence transformers / torch
        'sentence_transformers',
        'torch',
        'transformers',
        # ChromaDB
        'chromadb',
        'chromadb.config',
        'onnxruntime',
        # Keyring
        'keyring',
        'keyring.backends',
        'keyring.backends.macOS',
        # Laya modules (ensure all API routers are included)
        'laya.api.actions_api',
        'laya.api.audit_api',
        'laya.api.cards_api',
        'laya.api.connections_api',
        'laya.api.chat_api',
        'laya.api.dashboard_api',
        'laya.api.diagnostics_api',
        'laya.api.events',
        'laya.api.health',
        'laya.api.rules_api',
        'laya.api.settings_api',
        'laya.api.spaces_api',
        'laya.api.team',
        'laya.api.websocket',
        'laya.api.workspace_api',
        'laya.api.ws_router',
        'laya.pipeline.emit',
        'laya.pipeline.executor',
        'laya.pipeline.router',
        'laya.pipeline.stager',
        'laya.pipeline.space_resolution',
        'laya.pipeline.summarize',
        'laya.integrations.n8n_client',
        'laya.integrations.n8n_bootstrap',
        'laya.security.keychain',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test/dev packages to reduce size
        'pytest',
        'pytest_asyncio',
        'IPython',
        'jupyter',
        'matplotlib',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='laya-engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Engine is a headless server
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
