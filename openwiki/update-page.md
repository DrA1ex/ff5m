# Forge-X update page

Update availability uses `ScreenPage.UPDATE_NOTIFICATION`. The page header
shows `FORGE-X UPDATE: <available version>` once; the body contains release
notes and the LATER / UPDATE actions without an inner dialog frame or repeated
headings. Recovery and completion notices retain their existing dialogs.

## Layout

Release notes wrap within a 680-pixel text area and paginate seven rendered
rows at a time. The page has 24-pixel outer margins. Arrow centers align with
the first and last row positions, and the stacked page counter sits between
them. End arrows remain visible but have no active touch region. One-page
release notes, including the unavailable-note fallback, omit the pager.

Buttons retain their standard touch size. The gap above them on a full page
is approximately 44 pixels, twice the 22-pixel gap above the footer divider.
The shared header uses free width when there are no side controls, keeping
the normal font size for stable and beta release versions. With a back button,
header action, or busy notice, it retains the previous reserved title width.
The shared renderer change originates in Feather UI Designer and is
synchronized into the FF5M runtime.

## Ownership and compatibility

ForgeXUpdateNotification owns the offered version, release notes, and current
page index. Pagination derives the visible wrapped rows. Shared buttons,
arrows, and row geometry handle drawing and touch regions. The page does not
introduce another state owner, scroll subsystem, or update lifecycle.

LATER dismisses the current offer and returns home. A new revision may be
offered again. Print activity interrupts the page without dismissing the
offer; returning to safe idle presents its first page. Installation still
uses Moonraker's existing OTA operation and the existing safety, timeout,
retry, reset-confirmation, and completion paths. Recovery lists continue
to paginate four rows. Printer deployment is outside this change.

## Verification

Run `.venv/bin/python -m unittest tests.test_feather_update_notification` for
focused coverage, then `.venv/bin/python -m unittest discover -s tests` for
the repository suite. Coverage exercises full version visibility, seven-row
capacity, pager alignment and end controls, the 2:1 button gaps, wider
unwrapped notes, word-preserving wrapping across pages, unavailable notes,
scrolled-page dismissal and print interruption, and the existing update and
recovery contracts. Framework header-width coverage lives in the canonical
Designer repository. Visual expectations describe the standalone page.
