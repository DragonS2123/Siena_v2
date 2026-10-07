"""Install the user desktop entry for this checkout (no root required)."""
from pathlib import Path
import os


def quoted(value: Path) -> str:
    # Desktop Exec uses its own quoting, not shell quoting.
    text = str(value).replace('\\', '\\\\\\\\')
    for char in ('"', '`', '$'):
        text = text.replace(char, '\\\\' + char)
    return '"' + text.replace('%', '%%') + '"'


def main():
    root = Path(__file__).resolve().parents[1]
    directory = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / 'siena.desktop'
    entry.write_text(
        '[Desktop Entry]\nType=Application\nName=Siena\n'
        'Comment=Siena desktop assistant\n'
        f'Exec={quoted(root / "siena")}\n'
        f'Icon={root / "assets/siena.png"}\n'
        'Terminal=false\nCategories=Utility;\nStartupNotify=true\nStartupWMClass=Siena\n',
        encoding='utf-8',
    )
    print(entry)


if __name__ == '__main__':
    main()
