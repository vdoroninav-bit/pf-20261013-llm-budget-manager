"""`python -m bm_appimage.serve --host … --port …` — как `python -m bm.serve`, но с фабрикой AppImage."""

if __name__ == "__main__":
    import bm.serve

    bm.serve.APP = "bm_appimage.factory:create_app"
    bm.serve.main()
