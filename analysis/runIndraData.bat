@ECHO OFF
REM The Indra sign-in link is only asked for when no saved session exists.
REM Put your account email under "indra" in config.local.json or set INDRA_EMAIL.

@ECHO ON
python analysis\indradata.py %*
