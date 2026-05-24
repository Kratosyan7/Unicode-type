#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f hosts.txt ]]; then
    echo "hosts.txt не найден" >&2
    exit 1
fi

hosts=()
while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -z "$trimmed" ]] && continue
    [[ "$trimmed" == \#* ]] && continue
    hosts+=("$trimmed")
done < hosts.txt

if [[ ${#hosts[@]} -eq 0 ]]; then
    echo "В hosts.txt нет имён" >&2
    exit 1
fi

echo "Хосты (${#hosts[@]}):"
for h in "${hosts[@]}"; do echo "  - $h"; done

if [[ -f macos_payload.py ]]; then
    python3 host_guard.py pack macos_payload.py macos.enc "${hosts[@]}"
else
    echo "macos_payload.py не найден — пропускаю macos.enc"
fi

if [[ -f windows_payload.py ]]; then
    python3 host_guard.py pack windows_payload.py windows.enc "${hosts[@]}"
else
    echo "windows_payload.py не найден — пропускаю windows.enc"
fi

echo "Готово. Закоммить *.enc:"
echo "  git add macos.enc windows.enc && git commit -m 'Update trusted hosts' && git push"
