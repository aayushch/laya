; NSIS hooks injected into the Tauri-generated installer.
;
; Purpose: install the Microsoft Visual C++ 2015-2022 Redistributable on
; machines that don't already have it. ChromaDB's Rust bindings
; (chromadb_rust_bindings.*.pyd) link against vcruntime140_1.dll and
; msvcp140.dll which the python-build-standalone runtime we ship does
; not include. Without these DLLs the engine's lifespan crashes with
; "DLL load failed while importing chromadb_rust_bindings" on first run.
;
; The redistributable EXE is downloaded into resources/runtime/ during
; the Windows GitHub Actions build and shipped under $INSTDIR\resources\
; runtime\vc_redist.x64.exe.
;
; vc_redist exit codes we treat as success:
;   0    — newly installed
;   1638 — a newer version is already present
;   3010 — installed, reboot required
!macro NSIS_HOOK_POSTINSTALL
  ; Detect whether the VC++ 2015-2022 x64 runtime is already installed.
  ; Registry key written by every vc_redist.x64 release in this family.
  SetRegView 64
  ReadRegDWORD $1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
  SetRegView default

  StrCmp $1 1 vcredist_already_installed

  IfFileExists "$INSTDIR\resources\runtime\vc_redist.x64.exe" 0 vcredist_skip_missing
    DetailPrint "Installing Microsoft Visual C++ Redistributable (required for ChromaDB)..."
    ExecWait '"$INSTDIR\resources\runtime\vc_redist.x64.exe" /install /quiet /norestart' $0
    StrCmp $0 0 vcredist_done
    StrCmp $0 1638 vcredist_done
    StrCmp $0 3010 vcredist_done
    DetailPrint "VC++ Redistributable installer returned exit code $0 (continuing)"
    Goto vcredist_done

  vcredist_already_installed:
    DetailPrint "Microsoft Visual C++ Redistributable already installed, skipping"
    Goto vcredist_done

  vcredist_skip_missing:
    DetailPrint "VC++ Redistributable not bundled in this build, skipping"

  vcredist_done:
!macroend
