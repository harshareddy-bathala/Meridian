"""Owner reports: what each pass came to, and how the station's week went.

Module 16 of the architecture, Stage 29 of the roadmap. After each pass, a
station's owner is told in plain words what was received and its verdict, or
the diagnosed cause of the loss; once a week, how the station's week went
(D-098).

* **Templates, never a language model.** Every report is a versioned template
  filled with stored results, so it can print nothing the store does not hold,
  including "undetermined".
* **Regenerable.** A report keeps the rows it was read from and the hashes of
  what it read and what it said; rendering it again must reproduce both.
* **Team-operated stations only** (D-107 option b). No recipient is held in the
  database, in a log line or in any MSP message, and a simulated station's
  report goes only to a test sink.
* **Off every path that matters.** Reports are queued by the jobs service from
  the database alone and sent by a process of their own, so a failed delivery
  never delays or changes scheduling, reception or a stored result.

Its own module because it composes three others' outputs and sends through
outside services, and kept apart, that dependency lives in one place (D-102).

Reference: docs/DECISIONS.md D-098, D-102, D-107, D-300 to D-307.
"""
