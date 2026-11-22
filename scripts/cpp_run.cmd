@echo off
rem 本地命令行编译学生锁管理器草稿并生成回放（Web 不编译学生代码）。
rem 用法：scripts\cpp_run.cmd <student_locks.cpp> <outdir>
cd /d %~dp0..
if not exist tmp\cppbuild mkdir tmp\cppbuild
copy /y %1 tmp\cppbuild\lockcore_student.h >nul
.venv\Scripts\python.exe -m ziglang c++ -std=c++17 -O1 -DUSE_STUDENT_LOCKS -I tmp\cppbuild lab\cpp\teach.cpp -o bin\teach_student.exe
if errorlevel 1 exit /b 1
if not exist %2 mkdir %2
for %%s in (conflict rollback shared) do bin\teach_student.exe locks %%s > %2\%%s.jsonl
echo STUDENT_BUILD_OK %2