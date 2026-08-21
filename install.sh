#!/bin/sh
# Установка навыка Son of Orchestrator.
#
#   curl -fsSL https://raw.githubusercontent.com/kriscokitchen/son-of-orchestrator/main/install.sh | sh
#
# По умолчанию ставит для всех проектов, в ~/.claude/skills/.
# Флаг --project ставит только в текущий проект, в ./.claude/skills/.
# Переменная REF выбирает ветку или тег (по умолчанию main).

set -eu

REPO="kriscokitchen/son-of-orchestrator"
NAME="son-of-orchestrator"
REF="${REF:-main}"
SCOPE="user"

for arg in "$@"; do
  case "$arg" in
    --project) SCOPE="project" ;;
    --user) SCOPE="user" ;;
    -h|--help)
      echo "Установка навыка $NAME"
      echo
      echo "  sh install.sh            в ~/.claude/skills (для всех проектов)"
      echo "  sh install.sh --project  в ./.claude/skills (только этот проект)"
      echo "  REF=ветка sh install.sh  поставить из другой ветки"
      exit 0 ;;
    *) echo "Неизвестный аргумент: $arg. Смотри --help." >&2; exit 2 ;;
  esac
done

if [ "$SCOPE" = "project" ]; then
  DEST="$PWD/.claude/skills/$NAME"
else
  DEST="$HOME/.claude/skills/$NAME"
fi

command -v curl >/dev/null 2>&1 || { echo "Нужен curl, его нет в системе." >&2; exit 1; }
command -v tar  >/dev/null 2>&1 || { echo "Нужен tar, его нет в системе." >&2; exit 1; }

TMP=$(mktemp -d 2>/dev/null || mktemp -d -t "$NAME")
trap 'rm -rf "$TMP"' EXIT INT TERM

echo "Качаю $REPO ($REF)…"
if ! curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF" \
     | tar -xz -C "$TMP" 2>/dev/null; then
  # ref может быть тегом, а не веткой
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/tags/$REF" \
    | tar -xz -C "$TMP" || { echo "Не удалось скачать $REPO ($REF)." >&2; exit 1; }
fi

SRC=$(find "$TMP" -maxdepth 3 -type d -path "*/skills/$NAME" | head -1)
[ -n "$SRC" ] && [ -f "$SRC/SKILL.md" ] || {
  echo "В скачанном архиве нет skills/$NAME/SKILL.md — установка отменена." >&2
  exit 1
}

# Существующую копию не затираем молча: отодвигаем с меткой времени.
if [ -e "$DEST" ]; then
  BACKUP="$DEST.backup-$(date +%Y%m%d-%H%M%S)"
  mv "$DEST" "$BACKUP"
  echo "Прежняя версия отодвинута: $BACKUP"
fi

mkdir -p "$(dirname "$DEST")"
cp -R "$SRC" "$DEST"

PHASES=$(find "$DEST/phases" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
echo
echo "Готово: $DEST"
echo "SKILL.md и файлов фаз: $PHASES"
echo
echo "Осталось два шага:"
echo "  1. Поставить единственную зависимость, из корня проекта:"
echo "       npx impeccable install"
echo "  2. Перезапустить агента, чтобы он увидел навык."
echo
echo "Дальше: /$NAME и словами опиши, что нужно построить."
