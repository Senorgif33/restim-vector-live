# Contributing

Thank you for helping improve Vector 1A.

1. Create a branch from `main`.
2. Keep changes narrowly scoped and preserve deterministic queue timing.
3. Add or update tests for motion, timing, parsing, or output behavior.
4. Run `python -m unittest discover -s tests -v`.
5. Open a pull request describing the observed behavior and verification setup.

Never test an unreviewed change on connected stimulation hardware. Use ReStim's
graphical display with hardware disconnected first. Reports should omit private
media filenames, network addresses, and other personal information.
