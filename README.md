# TeX-DOCX Drafter (`texdocxdrafter`)

Drafts a clean, style-mapped Word document (`.docx`) from a LaTeX paper, for coauthors who
edit in Word.

It is a drafter, not a converter: it does not replicate the PDF. The goal is an editable base
document with proper style, auto-numbering and cross-references.

## Why I make this package

As a researcher writing in LaTeX, I frequently encounter coauthors wanting an editable Word file for
their editing and for the final submission.

So I need to hand them my draft, the whole paper or just a section, in a form they can continue to
edit: not just plain texts. 
It does not matter whether the page looks exactly like my PDF. 
Also, figures generated in LaTeX are converted to pictures and inserted into Word, so nobody has to redraw them elsewhere.

Many complex LaTeX features are not supported, because they cost only a little manual editing or a
macro in Microsoft Word, while supporting them would make this package complex.

The reference list is rendered as static text, because different scholars favour different tools:
Zotero, EndNote, Word's own citations, or static text. 
Automatic referencing is part of the downstream editing.

If you want an easily editable file to start downstream editing from, here it is. 
If you want to keep the exact layout, there are many other tools available. Please search for them
first.

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
- With `--comments`, LaTeX `%` comments in the document body become Word comments on the paragraph they annotate.
- A report lists every construct that was dropped or approximated, with `file:line`.

Pure Python plus your existing TeX installation. No pandoc, no LibreOffice, no Java.

## Requirements

- Python 3.12 or newer.
- The TeX distribution that compiled your paper. biblatex citations and TikZ pictures need two small side
  compiles with the same engine (`pdflatex`/`xelatex`/`lualatex`) and `biber`; see "Side compiles".
- `pdftotext`, `pdftoppm` and `pdfinfo` (poppler-utils). `gs` (Ghostscript) is needed only for EPS figures.
- Linux: `bwrap` (bubblewrap) lets the tool run the side compiles itself, in a sandbox. Without it the tool
  writes the side documents and you compile them.

## Install

```
pip install texdocxdrafter
```

Or, to get the command without managing a virtual environment, with [pipx](https://pipx.pypa.io/):

```
pipx install texdocxdrafter
```

This installs the `texdocxdrafter` command (also runnable as `python -m texdocxdrafter.cli`).
The name is `tex` + `docx` + `drafter`; `pip install tex-docx-drafter` resolves to the same package.
To work on the code itself, see Development at the end.

## Before you run it

`texdocxdrafter` works on a document you have already compiled. Next to `main.tex` there must be the `.aux` file
and the `.bbl` file (required for biblatex; for BibTeX the citations are read from it):

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

If `main.docx` already exists and is not byte-for-byte a file this tool wrote (for example because a coauthor
has saved it), the build stops rather than overwrite it. Pass `-o other.docx` or `--overwrite`. The same
protection covers the report.

In Word, press `Ctrl+A` then `F9` to refresh all fields (`texdocxdrafter` also sets "update fields on open", so
Word may ask whether to update them). After that, inserting or moving a section renumbers headings,
captions, equations and every cross-reference.

## Side compiles

Two things cannot be produced by parsing alone: biblatex citations, whose formatting lives in the style's TeX
macros, and `tikzpicture` environments, which are TeX programs. For them the tool builds two small side
documents from your own preamble, `harvest.tex` (every citation command and the bibliography on one huge page)
and `tikz.tex` (one page per picture), and needs their PDFs.

- **With bubblewrap (`bwrap`, Linux).** The tool compiles them itself, in a sandbox in which the engine sees
  only the system and TeX directories, your document's directories (read-only) and a private work directory,
  with no network. TeX's own cache (`TEXMFVAR`) is read-only too; the run gets a throwaway one that is deleted
  with the work directory, so a document cannot leave Lua code or a format file behind for a later compile.
- **Without bubblewrap (macOS, Windows, Linux without it).** The tool never starts a TeX engine. It writes the
  side documents into `main.texdocxdrafter/`, finishes the docx with citations as `[key]` and pictures as
  visible text, and the report gives the exact command to compile them, of the form

  ```
  cd main.texdocxdrafter && TEXINPUTS="/path/to/paper:" BIBINPUTS="/path/to/paper:" BSTINPUTS="/path/to/paper:" xelatex harvest && biber harvest && xelatex harvest && xelatex harvest
  ```

  Run it, then rerun `texdocxdrafter build`: the PDFs are picked up. A side document is rewritten only when
  its content changes, so this step is needed again only after you edit the preamble, a citation or a picture.
  The side document is your project's preamble: compile it only if you trust the project, exactly as when you
  compile the paper itself. A PDF counts only if it is newer than a side document this tool recorded writing,
  so a `harvest.pdf` that arrived inside an archive is ignored until you compile.

BibTeX documents (`\bibliography{...}` with natbib or plain `thebibliography` styles) need no side compile:
the `.bst` style has already typeset the entries into `main.bbl`, natbib's author-year labels are in it too,
and citations and the reference list are read from that file.

## Commands

| Command | Purpose |
|---|---|
| `texdocxdrafter build main.tex` | Convert to docx and write the report |
| `texdocxdrafter inventory main.tex` | Print a census of constructs and the Word style each maps to; create `stylemap.yaml` if none exists |
| `texdocxdrafter parse main.tex` | Dump the parsed document tree as JSON (`--facts` prints preamble facts only) |
| `texdocxdrafter harvest main.tex` | Print every citation and bibliography entry as the docx will show them (side compile or `.bbl`) |
| `texdocxdrafter check out.docx` | Style usage table and structural validation of a docx |

### `build` options

| Option | Effect |
|---|---|
| `-o out.docx` | Output path (the report is written next to it) |
| `--style-map file.yaml` | Use this style map instead of `main.texdocxdrafter/stylemap.yaml` |
| `--reference-doc tpl.docx` | Use a journal or house template. Style names in the style map must exist there; missing ones are created and reported |
| `--link-citations` | Make each citation a hyperlink to its bibliography entry |
| `-f`, `--overwrite` | Replace an existing output file even if it was not written by `texdocxdrafter` |
| `--keep-work` | Keep the intermediate files (harvest files, `tree.json`, images) under `main.texdocxdrafter/work/` |
| `--no-harvest` | Skip citations (no side compile, no `.bbl` reading); they appear as `[key]` |
| `--no-tikz` | Skip the `tikzpicture` side compile; pictures stay as visible text |
| `--comments` | Turn LaTeX `%` comments into Word comments (off by default, since source comments are often private) |
| `--allow-dir DIR` | Also accept `\input`, `\include` and graphics from `DIR` (repeatable). By default only files under the directory of `main.tex` are read |
| `--timeout SECONDS` | Limit for each TeX or biber run of the side compiles (default 120, at most 3600) |
| `--validate-libreoffice` | Also try to open the result with headless LibreOffice, if installed. It runs in the same sandbox as the TeX engines with a private profile; a snap-packaged LibreOffice cannot start there and is reported as such |
| `--strict` | Write nothing when the word-count check or the content check finds a loss (by default both are reported, and only structural validation and the policy check are fatal) |

With `--no-harvest --no-tikz` the tool only parses; the same is true for BibTeX documents without TikZ.

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
  package, one page per picture, labels taken from the `.aux`; see "Side compiles") and rasterised at 300 dpi. A picture inside a
  float keeps the float's font size and single spacing. If the compile fails, the source stays as visible text.
- Comments (`--comments`): a comment at the end of a text line, or on its own line within a paragraph, goes on that
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
  citations and entries are read from the document's own `.bbl` with no compile (natbib author-year and
  numeric forms, starred and capitalised variants, pre- and post-notes, the bracket and separator options,
  `sort&compress`, `\bibpunct`, `\setcitestyle`).
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

## What the tool does to your files

`texdocxdrafter` reads your sources and writes a docx. It does not compile your paper. biblatex citations and
TikZ pictures need two side compiles of your own preamble (see "Side compiles"), and the tool runs them itself
only inside bubblewrap: a sandbox in which the engine sees the system and TeX directories and the caches
(read-only; of `/etc` only the font, TeX, loader and Ghostscript configuration, and no `/proc`), your document's
directories (read-only) and a private work directory holding a throwaway `TEXMFVAR`, with no network, no
capabilities, shell escape disabled, `openout_any=p`, a filtered environment and limits on time, CPU and output
size (set through `prlimit` where util-linux provides it). Without
bubblewrap no TeX engine is started; you compile the side documents yourself, exactly as you compile the paper.
The report's run summary says which of the two happened. Image converters (poppler, Ghostscript) run under the
same limits. LuaTeX additionally loads a small guard that refuses file writes, links and directory changes
outside the work directory, running programs, loading native code and recovering the unguarded functions
through the `debug` library; the sandbox, not the guard, is the boundary. Whatever a compile or a converter
leaves in the work directory is treated as untrusted: the tool
reads only regular files there, never through a symbolic link, and creates its own files afresh.

- Files are written only to the output `.docx`, its report, and `main.texdocxdrafter/` (the style map, the hash
  registry, the side documents and, with `--keep-work`, a `work/` directory that is replaced on the next run
  only if this tool created it). `main.texdocxdrafter/` must be a real directory next to `main.tex`, and the
  side documents are replaced only if the registry says this tool wrote them; nothing is ever written through
  a symbolic link.
  Nothing else next to your sources is created, changed or deleted. The docx is written to a temporary name
  and renamed into place, so an interrupted run never leaves a half-written file.
- Nothing is published before it is checked. The docx is validated on a private copy in the work directory:
  it must be a readable package whose XML is well formed, whose styles, relationships, bookmarks and numbering
  resolve, and whose children sit in schema order; and the whole package is checked against the
  active-content policy below (every relationship, content type, part and field, wherever it sits). An error
  there ends the run with a message and the existing output, report and registry are untouched. The
  word-count check and the content check are reported in the same way but are fatal only with `--strict`.
  Every input (sources, `.aux`, `.bbl`, `.bib`, graphics) is hashed when it is read and checked again right
  before publication: if one changed meanwhile, nothing is written and the message names it.
- The output and the report are never source files or symbolic links. The tool records a hash of every file
  it writes in `main.texdocxdrafter/outputs.json`; an existing file whose hash does not match, that is, any
  file it did not write or that has been changed since, is not overwritten without `--overwrite`. Files are
  written through an exclusively created temporary file and renamed into place, and the target is checked
  again at that moment: a file that appeared or changed while the conversion ran is left alone. The report,
  the docx and the registry are committed as one step, in that order; if one of the three fails (disk full,
  permissions), the ones already written are put back to their previous content, and if that fails as well the
  run ends with a message naming the files that now disagree and asking for `--overwrite` on the next run. A
  new file gets the usual permissions (0666 minus your umask) and a replaced file keeps its mode. On drives
  without hard links (FAT, some network shares) the name is reserved by exclusive creation and the finished
  temporary file renamed onto it, so a disk-full error never leaves a truncated output. The output may not be
  the reference document. What a sequence of renames
  cannot promise: a crash between two of the three leaves them inconsistent (the next run will say so), the
  moment between the last identity check of the target and its rename remains open to a concurrent save by
  another program, and the directory is `fsync`ed after the rename only where the platform allows it, so
  durability across a power loss is best effort. A file at the
  registry's path that is not a registry this tool wrote, or that is one of your sources, stops the run.
- Source files must be UTF-8. Bytes that are not are replaced by U+FFFD and the report names the file and the
  offset of the first one; the files themselves are never modified. A `%` inside `\verb`, `\url`, `\href` or
  `\path` is text, not a comment.
- A reference document is used for its styles, numbering, page setup, headers and footers only. Its comments
  and people list, footnotes and endnotes, glossary, custom XML, custom and extended properties, thumbnail,
  every core property, attached-template path and document variables are removed first, so nothing of the
  template's own text or author travels into your draft. Macro-enabled templates (`.docm`, `.dotm`) are
  refused, and a VBA project inside a `.docx` is dropped. In its headers and footers only fields computed from
  the document itself survive (`PAGE`, `NUMPAGES`, dates, `STYLEREF`, `REF`, `SEQ` and the like); fields that
  make Word fetch other files or URLs (`INCLUDETEXT`, `INCLUDEPICTURE`, `LINK`, `DDE`, `MERGEFIELD`, …) or
  show your file path (`FILENAME`), alternate-format chunks, sub-documents, embedded objects and controls, and
  externally linked pictures are removed, as are mail-merge settings and their data sources, because the docx
  asks Word to update all fields on opening so that its own cross-references show the right numbers. The page
  background and its fill, web-layout frames, the view setting and editing or write protection are removed too.
  Hyperlinks in its headers and footers follow the same rule as the tool's own links: `http`, `https` and
  `mailto` targets stay links, anything else (network shares, `file:`, `javascript:`, relative paths) becomes
  plain text.
- The citation harvest marks its output with a nonce that the `.bib` files never see (random per run; kept in
  `main.texdocxdrafter/harvest.salt` when you compile the side document yourself), so text in a bibliography
  entry cannot pose as a marker and forge or truncate citations. Characters that XML cannot carry are dropped
  from all text before it is written.
- Build files (`.aux`, `.bbl`, `.log`) that are symbolic links to files outside the project are refused, and
  the TikZ side compile receives only the `\newlabel` lines of the `.aux`, never the raw file.
- Only `http`, `https` and `mailto` targets become Word hyperlinks; `\url` and `\href` with other schemes
  (`file:`, `javascript:`, network paths) stay plain text.
- `\input`, `\include` and graphics are read only from the directory of `main.tex`, symlinks resolved, unless
  you add directories with `--allow-dir`. A shared preamble in a parent directory therefore needs
  `--allow-dir ..`.
- Source comments stay out of the docx unless you pass `--comments`.
- The tool's own process runs under a memory cap (4 GiB of address space) and a CPU-time cap (30 minutes),
  also when `texdocxdrafter.pipeline.build` is called from Python; the report notes when a limit could not
  be applied. Style names from the style map must be printable text and are escaped when written into XML.
  Author macro expansion has a budget (20,000 expansions, 2 million characters in total, 200,000 per
  expansion); a runaway definition such as `\newcommand{\dbl}{\dbl\dbl}` stops there with an error in the
  report and the remaining calls kept as text. `\input` expansion stops after 500 files or 50 million
  characters, a table column spec is capped at 200 columns, and font declarations nest at most 50 deep. A
  document that nests groups thousands of levels deep is refused with a message rather than a crash.

## Reading the report

`main.texdocxdrafter-report.md` contains the run summary, style usage, construct census, and sections for
unsupported constructs, dropped presentation commands, direct formatting, math that stayed as LaTeX text,
missing figures, harvest problems (for example cited keys missing from the `.bib`), numbering
differences between LaTeX and Word, a word-count check that warns when more than 2 % of the source
words are missing from the docx, and a content check that compares the parsed document with the finished
file: every heading in order and with its text, and the number of tables, embedded pictures, equations,
footnotes and bibliography entries. The content check does not compare the order of body paragraphs, the
contents of table cells, citations (the harvest line gives their count), metadata or formatting.

## Known limits

- Appendix headings keep numeric numbering in Word (flagged in the report).
- `\numberwithin` (per-section equation numbers) is not reproduced; Word numbers continuously.
- A multi-line numbered `align` loses its alignment points (each numbered line is its own row);
  unnumbered `align*` keeps them.
- Math conversion uses `latex2mathml` and `mathml2omml`; unusual formulas fall back to LaTeX text, and a
  macro the converter does not know stays in the formula as its literal name (reported). `\intertext` becomes
  a body paragraph between two equation blocks.
- Word has no footnotes inside footnotes: an inner footnote is written in parentheses inside the outer one
  (reported). A `longtable` footer repeated on every page (`\endfoot`) is dropped (reported); the closing rows
  (`\endlastfoot`) follow the body.
- A float with several `\caption`s gives one captioned table or figure per caption; tabulars inside a figure
  (or pictures inside a table) follow the main block.
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

