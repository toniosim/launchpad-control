# Button map (Launchpad Pro MK3, Programmer mode)

Edge buttons arrive as `control_change`; their LED index should equal the CC number.
Fill this in from `scripts/lp_hello.py` output (press each button, note the CC).

| Buttons | CC | Status | Used for |
|---|---|---|---|
| Row just below the grid (8 buttons), left to right | 101–108 (from the programmer's reference diagram) | 102 verified on hardware 2026-11-14 (switches to the Claude bank); the rest follow the diagram | banks: 101 = main, 102 = Claude (`app.BANK_BUTTONS`) |
| Bottom-most row (8 buttons) | 1–8 (from the diagram) | unverified | |
| Top row | 91–98 (from the diagram) | unverified | |
| Left column, top to bottom | 80, 70, …, 10 (from the diagram) | unverified | |
| Right column, top to bottom | 89, 79, …, 19 (from the diagram) | unverified | |
