@ECHO OFF

IF NOT DEFINED SUNSYNK_USERNAME (
SET /p SUNSYNK_USERNAME="Enter Sunsynk Username:"
)

IF NOT DEFINED SUNSYNK_PASSWORD (
SET /p SUNSYNK_PASSWORD="Enter Sunsynk Password:"
)

@ECHO ON
python analysis\collectdata.py %*

