# Commands

## Auth

```bash
rh auth set-key YOUR_KEY
rh auth show
rh auth set-output-dir ./output
```

## Account Check

```bash
rh check
rh --json check
```

## Models

```bash
rh model list --type video
rh model list --task image-to-video
rh model info rhart-video-v3.1-fast/text-to-video
rh model run --endpoint rhart-image-n-pro/text-to-image -p "a cute dog"
rh model run --task text-to-image -p "a cute dog"
```

## Shortcuts

`rh image` uses the curated 5-image-model menu. `rh video` uses the curated 8-video-model menu.

Use `--model` to skip the interactive menu:

```bash
rh image --model 1 -p "a cute dog"
rh video --model "可灵" -p "a person walking in rain"
```

## AI Apps

```bash
rh app list --sort RECOMMEND
rh app list --sort HOTTEST --days 7
rh app info WEBAPP_ID_OR_URL
rh app run WEBAPP_ID_OR_URL --node "52:prompt=..." --file "39:image=./input.png"
```
