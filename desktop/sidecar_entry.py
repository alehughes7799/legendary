"""PyInstaller entry point; freeze support must run before importing the core."""

from multiprocessing import freeze_support

if __name__ == '__main__':
    freeze_support()
    from legendary.bridge.__main__ import main

    main()
