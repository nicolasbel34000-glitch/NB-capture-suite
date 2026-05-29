from .windowing import configure_process_dpi_awareness

configure_process_dpi_awareness()

from .app import main


if __name__ == "__main__":
    raise SystemExit(main())
