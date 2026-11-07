@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Copyright (C) 2026 pyLogi contributors
rem
rem pyLogi launcher for Windows. The application always runs in its own virtual environment
rem (.venv next to this file), which is created and filled on first use.
rem
rem   pylogi.cmd [pyLogi options]   start the application (options of "pylogi --help" are passed through)
rem   pylogi.cmd --setup            create / update the environment and exit
rem   pylogi.cmd --recreate         delete the environment, build it again and exit
rem   pylogi.cmd --print-env        print the environment folder and exit
rem
rem Environment variables: PYLOGI_VENV (environment folder), PYLOGI_PYTHON (interpreter used to create it),
rem PYLOGI_SYSTEM_SITE=1 (let it see the system packages), PYLOGI_NO_INSTALL=1 (do not run pip).
rem
rem Style rule: no multi-line ( ) blocks. A path such as "C:\Program Files (x86)\..." expanded inside a block
rem would end it early, so the flow uses labels and goto instead.
setlocal EnableExtensions

set "HERE=%~dp0"
if not defined PYLOGI_VENV set "PYLOGI_VENV=%HERE%.venv"
set "VPY=%PYLOGI_VENV%\Scripts\python.exe"
set "VPYW=%PYLOGI_VENV%\Scripts\pythonw.exe"
set "STAMP=%PYLOGI_VENV%\requirements.installed"
set "SETUP_ONLY="

if /i "%~1"=="--print-env" goto :printenv
if /i "%~1"=="--recreate" goto :recreate
if /i "%~1"=="--setup" set "SETUP_ONLY=1"
goto :main

:printenv
echo %PYLOGI_VENV%
exit /b 0

:recreate
if exist "%PYLOGI_VENV%" rmdir /s /q "%PYLOGI_VENV%"
set "SETUP_ONLY=1"

:main
call :setup
if errorlevel 1 exit /b 1
if defined SETUP_ONLY goto :ready
set "PYLOGI_LAUNCHER=%HERE%pylogi.cmd"
if not defined PYLOGI_SYSTEM_SITE set "PYTHONNOUSERSITE=1"

rem Window options start the graphical interface without a console; every other option keeps the console for its output.
set "GUI="
if "%~1"=="" set "GUI=1"
if /i "%~1"=="--minimized" set "GUI=1"
if /i "%~1"=="--demo" set "GUI=1"
if /i "%~1"=="--no-splash" set "GUI=1"
if /i "%~1"=="--offline" set "GUI=1"
if /i "%~1"=="--lang" set "GUI=1"
if /i "%~1"=="--theme" set "GUI=1"
if /i "%~1"=="--appearance" set "GUI=1"
if defined GUI goto :gui
"%VPY%" "%HERE%run.py" %*
exit /b %errorlevel%

:gui
start "" "%VPYW%" "%HERE%run.py" %*
exit /b 0

:ready
echo pyLogi: environment ready: %PYLOGI_VENV%
exit /b 0

:setup
if exist "%VPY%" goto :deps
call :findpython
if errorlevel 1 goto :nopython
echo pyLogi: creating the virtual environment in %PYLOGI_VENV%
if defined PYLOGI_SYSTEM_SITE goto :venv_system
%PYCMD% -m venv "%PYLOGI_VENV%"
goto :venv_done
:venv_system
%PYCMD% -m venv --system-site-packages "%PYLOGI_VENV%"
:venv_done
if errorlevel 1 goto :novenv
:deps
if defined PYLOGI_NO_INSTALL goto :verify
fc /b "%HERE%requirements.txt" "%STAMP%" >nul 2>&1
if not errorlevel 1 goto :verify
echo pyLogi: installing the dependencies (needs an internet connection the first time)
"%VPY%" -m pip install --quiet --disable-pip-version-check -r "%HERE%requirements.txt"
if errorlevel 1 goto :nopip
copy /y "%HERE%requirements.txt" "%STAMP%" >nul
:verify
"%VPY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 goto :nopyside
exit /b 0

:nopython
echo pyLogi: Python 3.10 or newer is required. Install it from python.org, or set PYLOGI_PYTHON.
exit /b 1
:novenv
echo pyLogi: could not create the virtual environment.
exit /b 1
:nopip
echo pyLogi: pip could not install the dependencies.
exit /b 1
:nopyside
echo pyLogi: PySide6 is not available in %PYLOGI_VENV% - run pylogi.cmd --recreate
exit /b 1

:findpython
if defined PYLOGI_PYTHON goto :usegiven
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto :usepy
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto :usepython
exit /b 1
:usegiven
set PYCMD="%PYLOGI_PYTHON%"
exit /b 0
:usepy
set "PYCMD=py -3"
exit /b 0
:usepython
set "PYCMD=python"
exit /b 0
