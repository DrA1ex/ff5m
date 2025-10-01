@echo off
setlocal enabledelayedexpansion

REM Check if file parameter was provided
if "%~1"=="" (
echo Error: No file specified
pause
exit /b 1
)

REM Set the input file
set "inputfile=%~1"

REM Generate MD5 hash
for /f "skip=1 delims=" %%i in ('certutil -hashfile "%inputfile%" md5') do (
set "hash=%%i"
goto :got_hash
)

:got_hash
REM Remove spaces from hash
set "hash=!hash: =!"

REM Create the MD5 comment line
set "md5_line=; MD5:!hash!"

REM Create temporary file with MD5 header and original content
echo !md5_line! > "%TEMP%\gcode_temp.txt"
type "%inputfile%" >> "%TEMP%\gcode_temp.txt"

REM Replace original file with new content
move "%TEMP%\gcode_temp.txt" "%inputfile%" >nul 2>&1

if errorlevel 1 (
echo Error: Could not update file
pause
exit /b 1
)

echo MD5 hash added successfully
