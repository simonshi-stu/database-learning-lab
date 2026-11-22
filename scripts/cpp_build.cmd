@echo off
rem 构建 C++ 教学引擎：优先 ziglang（pip install ziglang），回退 g++ / MSVC cl。
cd /d %~dp0..
if not exist bin mkdir bin
.venv\Scripts\python.exe -m ziglang c++ -std=c++17 -O1 lab\cpp\teach.cpp -o bin\teach.exe 2>nul
if not errorlevel 1 goto ok
where g++ >nul 2>nul
if not errorlevel 1 (
  g++ -std=c++17 -O1 lab/cpp/teach.cpp -o bin/teach.exe
  if not errorlevel 1 goto ok
)
where cl >nul 2>nul
if not errorlevel 1 (
  cl /nologo /std:c++17 /O1 /EHsc lab\cpp\teach.cpp /Fe:bin\teach.exe
  if not errorlevel 1 goto ok
)
echo NO_COMPILER: pip install ziglang 或安装 g++ / MSVC 后重试
exit /b 1
:ok
echo BUILD_OK bin\teach.exe