#set page(paper: "us-letter", margin: (x: 0.55in, y: 0.5in))
#set text(font: "New Computer Modern", size: 10pt)
#set par(leading: 0.5em, justify: false)
#set list(tight: true, spacing: 0.55em, indent: 6pt, body-indent: 5pt)
#show heading.where(level: 2): it => block(width: 100%, above: 9pt, below: 7pt)[
  #set text(size: 10.5pt, weight: "bold")
  #smallcaps(it.body)
  #v(-7pt)
  #line(length: 100%, stroke: 0.6pt)
]
#show link: set text(fill: rgb("#1a3d6d"))

#align(center)[
  #text(size: 19pt, weight: "bold")[Jordan Rivera]
  #v(-2pt)
  Cambridge, MA #h(4pt)|#h(4pt) 617-555-0142 #h(4pt)|#h(4pt) jordan.rivera\@example.edu #h(4pt)|#h(4pt) #link("https://github.com/jrivera")[github.com/jrivera] #h(4pt)|#h(4pt) #link("https://linkedin.com/in/jordanrivera")[linkedin.com/in/jordanrivera]
]

== Education

#grid(columns: (1fr, auto), row-gutter: 2pt,
  [*Massachusetts Institute of Technology*], [#align(right)[*Sep 2024 -- May 2028*]],
  [_B.S. Computer Science & Applied Mathematics_], [#align(right)[_Cambridge, MA_]],
)
GPA: 3.8/4.0 \
*Relevant coursework:* Numerical Analysis, Probability & Statistics \
_Available for Summer 2027 internships_

== Experience

#grid(columns: (1fr, auto), row-gutter: 2pt,
  [*MIT — 6.006 Introduction to Algorithms*], [#align(right)[*Sep 2025 -- Dec 2025*]],
  [_Teaching Assistant_], [#align(right)[_Cambridge, MA_]],
)
- Held weekly office hours for 40+ students on dynamic programming, graph algorithms, and NP-completeness
- Rewrote 3 autograder test suites in Python, cutting false-positive failures reported by students in half

== Research

#grid(columns: (1fr, auto), row-gutter: 2pt,
  [*MIT Department of Mathematics*], [#align(right)[*Sep 2025 -- Present*]],
  [_Undergraduate Researcher_], [#align(right)[_Cambridge, MA_]],
)
- Implemented a Monte Carlo simulation to estimate convergence rates of a stochastic gradient method under noisy gradients
- Presented findings at the department's undergraduate research symposium to an audience of 50+

== Projects

#grid(columns: (1fr, auto), row-gutter: 2pt,
  [*xjobscore job-posting pipeline* #text(size: 8.5pt)[(#link("https://github.com/jrivera/xjobscore")[link])]], [#align(right)[*May 2026 -- Present*]],
  [_Solo developer_ — Python, asyncpg, PostgreSQL], [#align(right)[]],
)
- Built an async job-posting pipeline ingesting 2,600+ company feeds; normalized 6 ATS formats into one canonical schema
- Cut harvest wall-clock 70% by bounding concurrency with asyncio semaphores and per-host rate limits

== Skills

*Languages:* Python, C++ \
*Tools:* PostgreSQL \
*Math & Methods:* Monte Carlo simulation
