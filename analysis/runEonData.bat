@ECHO OFF
REM The E.ON Next browser sign-in is only asked for when no saved session exists.
@ECHO ON
python analysis\eondata.py %*
