# Output

RH CLI writes generated media to a local path and prints the path after completion.

Default output directory:

- Unix: `~/rh-output`
- Windows: `%USERPROFILE%\\rh-output`

Override it globally:

```bash
rh auth set-output-dir ./output
rh --output-dir ./output video -p "a cinematic dog"
```

Override it per command:

```bash
rh image -p "a cat" -o ./cat.png
rh app run 1877265245566922800 -o ./app-result.png
```

When a task returns multiple files, RH CLI appends `_1`, `_2`, and so on.

Use `--json` for scripts:

```bash
rh --json image --model 1 -p "a cat"
```
