@ECHO OFF
REM Phone number / SMS code are prompted for only when no saved session exists.
REM Set FUSE_PHONE to skip the phone prompt.

@ECHO ON
python analysis\fusedata.py %*
