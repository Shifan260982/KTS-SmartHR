# Builds build\SmartHR-Setup.exe: bundled Python + packages, the built portal, Ollama + qwen3:4b, the ONNX models and .env.
# Run from the repo root on a machine where the dev setup in README.md works:  powershell -File packaging\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$stage = Join-Path $root 'build\stage'
$pythonHome = (& "$root\.venv\Scripts\python.exe" -c "import sys; print(sys.base_prefix)").Trim()
$ollamaHome = "$env:LOCALAPPDATA\Programs\Ollama"
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"

function Copy-Tree($from, $to, [string[]]$excludeDirs = @()) {
    robocopy $from $to /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 /XD __pycache__ @excludeDirs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $from" }
}

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }

Write-Host 'Building portal...'
npm --prefix "$root\frontend" run build
if ($LASTEXITCODE) { throw 'Portal build failed' }

Write-Host 'Copying app...'
foreach ($dir in 'app', 'scripts', 'samples', 'tests') { Copy-Tree "$root\$dir" "$stage\$dir" }
Copy-Tree "$root\frontend" "$stage\frontend" @('node_modules')
Copy-Item "$root\.env", "$root\README.md", "$root\requirements.txt", "$root\packaging\launcher.py" $stage

Write-Host 'Copying Python...'
Copy-Tree $pythonHome "$stage\python" @('Doc', 'test', 'idlelib', 'tkinter', 'turtledemo')
Copy-Tree "$root\.venv\Lib\site-packages" "$stage\python\Lib\site-packages"

Write-Host 'Copying Ollama (CPU, NVIDIA CUDA 12 and Vulkan)...'
Copy-Item "$ollamaHome\ollama.exe" (New-Item "$stage\ollama" -ItemType Directory -Force)
Copy-Tree "$ollamaHome\lib" "$stage\ollama\lib" @('rocm_v7_1', 'cuda_v13')
Copy-Tree "$env:USERPROFILE\.ollama\models" "$stage\ollama\models"

Write-Host 'Copying ONNX models...'
Copy-Tree "$env:TEMP\fastembed_cache" "$stage\models\fastembed"
Copy-Tree "$env:USERPROFILE\.cache\huggingface\hub\models--Xenova--nli-deberta-v3-small" "$stage\models\huggingface\hub\models--Xenova--nli-deberta-v3-small"

Write-Host 'Compiling installer...'
& $iscc "$PSScriptRoot\smarthr.iss"
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
Write-Host "Done: $root\build\SmartHR-Setup.exe"
