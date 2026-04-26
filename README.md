# DatLang

A data-centric paradigm programming language that mainly focuses on how to combine database organization and functional programming.

DatLang is designed to easily query and manipulate data structures like lists of records (JSON, CSV, TSV) using SQL-like syntax mixed with traditional imperative and functional programming constructs.

## Features

- **Data-centric Syntax**: Native support for `FROM ... WHERE ... SELECT ...` queries.
- **Variables & Arithmetic**: Use `LET` for variable assignment and standard arithmetic operators.
- **Control Flow**: Conditional branching with `IF ... ELSE ...` and iteration with `FOR each ... IN ...`.
- **I/O**: Print results to standard output and easily `EXPORT` results to JSON, CSV, or TSV formats.
- **Data Integrations**: Seamlessly read data from JSON, CSV, or TSV files directly into the execution environment.

## Usage

DatLang is implemented in Python. 

```bash
python3 datlang.py <source.dat> [--debug] [--env <data-file>]
```

### Arguments:
- `<source.dat>`: Path to a DatLang source file (`.dat`).
- `--debug`: Show token list and AST before running.
- `--env <file>`: Data file to load into the environment. Supported formats (auto-detected by extension):
  - `.json` — JSON object mapping table names to row lists
  - `.csv` — CSV file; loaded as table named by the filename stem
  - `.tsv` — TSV file; same as CSV but tab-separated

If no source file is given, a built-in demo is run.

## Example

```datlang
# Define thresholds using LET + arithmetic
LET pass_mark = 50
LET bonus     = 5
LET effective = pass_mark + bonus

# Query passing students
result = FROM students
         WHERE score > effective
         SELECT name, score

# Iterate and format
PRINT ">> Passing Students:"
FOR each student IN result:
    IF student.score > 85:
        PRINT student.name + " (Honored)"
    ELSE:
        PRINT student.name

# Export results
EXPORT result TO "./Output/passing.csv"
```

## Group members:
- 6600685 - ออมพล โคตรสุโน
- 6602024 - ปภังกร ธรรมสุขสรรค์
- 6601398 - ชวกร ทุมมา
