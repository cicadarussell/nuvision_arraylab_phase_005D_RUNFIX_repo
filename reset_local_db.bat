@echo off
setlocal
cd /d %~dp0
if exist backend\nuvision_arraylab_dev.db (
  echo Deleting backend\nuvision_arraylab_dev.db
  del backend\nuvision_arraylab_dev.db
) else (
  echo No local SQLite DB found.
)
echo Done. The backend will recreate tables on next startup.
