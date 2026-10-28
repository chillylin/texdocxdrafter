# TeX-DOCX Drafter (`texdocxdrafter`)

Drafts a clean, style-mapped Word document (`.docx`) from a compiled LaTeX paper, for coauthors who
edit in Word.

It is a drafter, not a converter: it does not try to copy the PDF. The goal is an editable base
document where one change to a style changes every instance, and where numbering and
cross-references still work after you edit.

## Why I need this package

I am a researcher writing in LaTeX, but in many cases my coauthors want an editable Word file for
their editing and for the final submission.

So I need to hand them my draft, the whole paper or just a section, in a form they can continue to
edit: with correct styles and cross-references, which make downstream editing easy. It does not
matter whether the page looks exactly like my PDF. Figures generated in LaTeX are converted to
pictures and inserted into Word, so nobody has to redraw them elsewhere.

Many complex LaTeX features are not supported, because they cost only a little manual editing or a
macro in Word, while supporting them would cost a lot and make this package complex.

The reference list is rendered as static text, because different scholars favour different tools:
Zotero, EndNote, Word's own citations, or by hand. Automatic referencing is part of the downstream
editing, not of this package.

If you want to keep the exact layout, there are many other tools available. Please search for them
first. But if you want an easily editable file to start downstream editing from, here it is.

I intend to keep the package small and simple. You are welcome to fork it and make it fancier.

Good luck.

What you get:

- Every structural element uses a named Word style (Heading 1, Body Text, Caption Table, Bibliography, ...).
- Headings, table and figure captions, and equations use Word's own auto-numbering.
- `\ref`, `\cref`, `\autoref`, `\eqref` and `\pageref` become live REF/PAGEREF fields.
- Citations and the reference list are rendered by the real biblatex or BibTeX (italics, punctuation, style)
  and inserted as plain formatted text. There are no Zotero or Word citation fields.
- Review markup such as `\add{...}` and `\del{...}` becomes Word tracked changes (insertions and deletions).
- A two-column LaTeX layout becomes a two-column Word layout.
- Equations become native Word (OMML) equations.
- `tikzpicture` environments are compiled with your TeX engine and embedded as PNG images.
- LaTeX `%` comments in the document body become Word comments on the paragraph they annotate.
- A report lists every construct that was dropped or approximated, with `file:line`.

Pure Python plus your existing TeX installation. No pandoc, no LibreOffice, no Java.

## Requirements

- Python 3.12 or newer.
- A TeX distribution with `pdflatex`/`xelatex`/`lualatex` and `biber` (used for the citation side compile).
- `pdftotext` and `pdftoppm` (poppler-utils). `gs` (Ghostscript) is needed only for EPS figures.

## Install

```
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs the `texdocxdrafter` command (also runnable as `python -m texdocxdrafter.cli`).
The name is `tex` + `docx` + `drafter`; PyPI treats `tex-docx-drafter` as the same project.

## Before you run it

`texdocxdrafter` works on a document you have already compiled. Next to `main.tex` there must be the `.aux` file
and, if you use biblatex, the `.bbl` file:

```
pdflatex main.tex && biber main && pdflatex main.tex && pdflatex main.tex
```

If they are missing, `texdocxdrafter` stops and tells you to compile first. For documents without citations
or when you only want the structure, use `--no-harvest`.

## Quick start

```
texdocxdrafter build main.tex
```

This writes `main.docx` and `main.texdocxdrafter-report.md` next to `main.tex`. Open the report first: it lists
what needs manual attention.

In Word, press `Ctrl+A` then `F9` to refresh all fields (`texdocxdrafter` also sets "update fields on open", so
Word may ask whether to update them). After that, inserting or moving a section renumbers headings,
captions, equations and every cross-reference.

## Commands

| Command | Purpose |
|---|---|
| `texdocxdrafter build main.tex` | Convert to docx and write the report |
| `texdocxdrafter inventory main.tex` | Print a census of constructs and the Word style each maps to; create `stylemap.yaml` if none exists |
| `texdocxdrafter parse main.tex` | Dump the parsed document tree as JSON (`--facts` prints preamble facts only) |
| `texdocxdrafter harvest main.tex` | Run only the citation side compile and print every citation and bibliography entry |
| `texdocxdrafter check out.docx` | Style usage table and structural validation of a docx |

### `build` options

| Option | Effect |
|---|---|
| `-o out.docx` | Output path (the report is written next to it) |
| `--style-map file.yaml` | Use this style map instead of `main.texdocxdrafter/stylemap.yaml` |
| `--reference-doc tpl.docx` | Use a journal or house template. Style names in the style map must exist there; missing ones are created and reported |
| `--link-citations` | Make each citation a hyperlink to its bibliography entry |
| `--keep-work` | Keep the work directory `main.texdocxdrafter/` (harvest files, `tree.json`, images) for inspection |
| `--no-harvest` | Skip the citation side compile; citations appear as `[key]` |
| `--no-comments` | Do not turn LaTeX `%` comments into Word comments |
| `--validate-libreoffice` | Also try to open the result with headless LibreOffice, if installed |

## Customising styles

Run `texdocxdrafter inventory main.tex` once. It writes `main.texdocxdrafter/stylemap.yaml` and never overwrites an
existing one. Edit it to rename Word styles, then run `build` again. Sections:

- `paragraph`: Word paragraph style per construct (`body`, `first_paragraph`, `quote`, `caption_table`,
  `table_text`, `bibliography`, ...). `heading` is the base name, giving Heading 1 to Heading 9.
- `character`: character styles (`emph`, `strong`, `code`, `link`, `citation`, ...).
- `options`:
  - `heading_offset`: `auto` (article: `\section` is Heading 1; report/book: `\chapter` is Heading 1) or an integer.
  - `caption_position`: `above` or `below` for tables and figures.
  - `paragraph_as_heading`: `false` makes `\paragraph{X}` a run-in bold sentence; `true` makes it a heading.
  - `strip_bold_in_headings`: unwrap `\textbf` inside headings (default true).
  - `reference_doc`: path of a template docx.
  - `table_style`: name of the table style.
  - `columns`: `auto` (two columns when the LaTeX layout has them), `1` or `2`.
  - `revision_macros`: `auto`, a mapping such as `{add: ins, del: del}`, or `false` to expand the macros as text.
  - `revision_author`: author name on tracked changes (default `LaTeX`).
  - `revision_environments`: environments whose content is a tracked change, default
    `{addblock: ins, delblock: del}`. A map you give replaces the default.
  - `inline_math_as_text`: `false` keeps every inline formula as a Word equation.

To change fonts, spacing or colours, edit the styles in Word once, or pass your journal template with
`--reference-doc`.

## Tracked changes from review markup

Revision markup in the LaTeX source becomes Word tracked changes, so a coauthor can accept or reject
each change in Word. Define the macros and environments in your preamble as you like; only their
effect in LaTeX matters, not their names:

```latex
\usepackage[dvipsnames]{xcolor}
\usepackage{ulem}
\newcommand{\add}[1]{\uline{#1}}            % underlined in the PDF  -> tracked insertion
\newcommand{\del}[1]{\sout{#1}}             % struck out in the PDF  -> tracked deletion
\newenvironment{addblock}{\color{NavyBlue}}{}   % colour only in the PDF -> tracked insertion
\newenvironment{delblock}{\color{red}}{}        % colour only in the PDF -> tracked deletion
```

Inline changes:

```latex
The sample holds \del{five} \add{four} industries.
```

Block changes, for passages that `\add{...}` cannot hold (headings, labels, lists, floats, footnotes):

```latex
\begin{delblock}
This paragraph is removed in the revision.

\subsection{A heading that goes too}
Its text, with a footnote.\footnote{Removed with it.}
\end{delblock}

\begin{addblock}
This paragraph is new in the revision.
\begin{itemize}
  \item so is this list
\end{itemize}
\end{addblock}
```

In the PDF the environments only colour the text, so the LaTeX document compiles as usual. In the docx
every paragraph, heading, list item, table row, caption and footnote inside `delblock` is a tracked
deletion and inside `addblock` a tracked insertion. Accepting all changes yields the revised text;
rejecting all yields the original.

How the tool decides:

- An author macro whose definition strikes out its argument (`\sout`, `\xout`) is a deletion; one that
  underlines it (`\uline`, `\uuline`, `\uwave`) is an insertion. Other macro names work the same way.
- The environment names `addblock` and `delblock` are the defaults of `options.revision_environments`
  in the style map; set your own map, for example `{newtext: ins, oldtext: del}`, to use other names.
- Text deleted inside an insertion nests as Word does. A lone space between `\del{a} \add{b}` is dropped,
  so accepting or rejecting all changes leaves single spacing.
- The author shown on the changes is `options.revision_author` (default `LaTeX`).
- Set `options.revision_macros: false` to turn the detection off and keep the macros as plain text.

## What is supported

- Sectioning, paragraphs, run-in `\paragraph`, appendices (flagged), quotes, verbatim, footnotes.
- Emphasis and bold, monospace, links (`\url`, `\href`), special characters and accents.
- `itemize`, `enumerate`, `description`, nested; enumitem `label=` with `\arabic*`, `\alph*`, `\roman*`;
  `\item[label]` in `itemize`/`enumerate` replaces the bullet or number with the label.
- Tables: `tabular`, `tabularx`, `tabulary`, `longtable`, `threeparttable` notes, booktabs and `\hline`
  rules, `\multicolumn`, `\multirow`. Rows above the first mid rule become repeating header rows.
- Figures: `\includegraphics` (PDF, EPS, PNG, JPEG), `subfigure`/`subcaption` panels, `\graphicspath`.
- TikZ: every `tikzpicture` is drawn in one side compile (the document's own preamble plus the `preview`
  package, one page per picture, labels taken from the `.aux`) and rasterised at 300 dpi. A picture inside a
  float keeps the float's font size and single spacing. If the compile fails, the source stays as visible text.
- Comments: a comment at the end of a text line, or on its own line within a paragraph, goes on that
  paragraph; a comment line between blocks goes on the next block. Consecutive comment lines become one
  Word comment (author "LaTeX"). Comments in the preamble, inside a `tikzpicture`, and empty or decorative
  ones (`%%%%`) are skipped.
- Math: inline, `equation`, `align`, `gather`, `multline`, `eqnarray`, labels and `\nonumber`.
- Simple inline math (`$\alpha = 0.63$`, `$R^2$`, `$p_{ik} \in [0,1]$`) is written as plain characters: Unicode
  Greek and symbols, sub/superscripts as raised or lowered text, LaTeX spacing around relations and operators.
  Latin letters and lowercase Greek use the italic character style "Math Variable". Formulas with fractions,
  big operators, accents or font switches stay native Word equations. Set `options.inline_math_as_text: false`
  in the style map to keep every formula as an equation.
  Numbered equations use a borderless layout table with a `SEQ` number.
- Author macros: `\newcommand`, `\renewcommand`, `\def`, `\newenvironment`, `\DeclareMathOperator`,
  `\newif` conditionals and `\iffalse`.
- Citations: the biblatex cite family (including `\cites` forms and natbib-style `\citet`/`\citep` with
  `natbib=true`), `\printbibliography`, `refsection`. BibTeX with natbib or plain `thebibliography` styles:
  `\bibliography{...}` is rendered by `bibtex` with the document's own `.bst` in the side compile.
- Review markup: `\add{...}`, `\del{...}`, `addblock` and `delblock` become tracked changes; see
  "Tracked changes from review markup" above.
- Two columns (`twocolumn` option, `\twocolumn` in the preamble or in a local `.cls`/`.sty` such as
  `acl.sty`): the body is a two-column Word section; the title block and `table*`/`figure*` sit in
  one-column sections joined by continuous section breaks. Tables and pictures inside a column are sized
  to the column. Word lays this out correctly. LibreOffice 26.2 opens the file but can fail to export it
  to PDF when footnotes fall in a two-column section that ends at a break; use `columns: 1` if you need
  LibreOffice.
- Front matter: `\title`, `\author` (with `\and`, `\thanks`), `\date`, `\maketitle`, `abstract`, `\keywords`.

## What is not

- Page layout, fonts, spacing, float placement, line breaks.
- Live citation fields, editable TikZ drawings (pictures arrive as images), beamer, three or more columns.
- Colour that is not inside a recognised review macro (`{\color{X} ...}` around a section is dropped).
- Arbitrary TeX. Anything outside the list above is kept as visible red text (style "Drafter Todo") and
  listed in the report with its source line, so the manual fix is bounded.

## Reading the report

`main.texdocxdrafter-report.md` contains the run summary, style usage, construct census, and sections for
unsupported constructs, dropped presentation commands, direct formatting, math that stayed as LaTeX text,
missing figures, harvest problems (for example cited keys missing from the `.bib`), numbering
differences between LaTeX and Word, and a word-count check that warns when more than 2 % of the source
words are missing from the docx.

## Known limits

- Appendix headings keep numeric numbering in Word (flagged in the report).
- `\numberwithin` (per-section equation numbers) is not reproduced; Word numbers continuously.
- A multi-line numbered `align` loses its alignment points (each numbered line is its own row);
  unnumbered `align*` keeps them.
- Math conversion uses `latex2mathml` and `mathml2omml`; unusual formulas fall back to LaTeX text.
- Verify the result in Word at least once per document: open it, update fields, and insert a section
  to confirm renumbering.

## License

MIT. See `LICENSE`.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

The unit tests in `tests/unit` are self-contained. End-to-end checks on real documents are run outside
this repository, since the test corpora are not shipped.
