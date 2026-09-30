@echo off
rem Full crawl of the source websites into corpus-exim, one site after another.
rem Run by the scheduled task "Exim crawl" so it outlives terminals and Claude
rem sessions.
rem
rem Two passes per site: the second re-reads every listing and picks up pages a
rem dropped connection made the first pass miss. Safe to start again after an
rem interruption: pages already saved are skipped.
rem
rem No indexing here: the running exim server indexes new files by itself every
rem AUTO_INGEST_SECONDS (default 60). A second indexer in this process would
rem overwrite the server's vectors. If the server is down, it catches up on start.
rem
rem Watch:  Get-Content D:\ALFA\exim\data\crawl.log -Wait -Tail 20
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set LOG=data\crawl.log

rem Vietnam first while its corpus is new; then the Indonesian sites, smallest
rem first, which by now only re-check their listings for new pages.
for %%S in (cong-bao pen daglu e-bpom bi bpom e-ska lpei beacukai jdih-kemenkeu) do (
    echo ===== !date! !time!  %%S pass 1 ===== >> %LOG%
    .venv\Scripts\python.exe main.py crawl --site %%S --full --no-ingest >> %LOG% 2>&1
    echo ===== !date! !time!  %%S pass 2 ===== >> %LOG%
    .venv\Scripts\python.exe main.py crawl --site %%S --full --no-ingest >> %LOG% 2>&1
)
echo ===== !date! !time!  done ===== >> %LOG%
