"""
PDF Impostor – Entry Point
==========================
Run with:   python main.py
Build with: python build.py
"""

import sys
import argparse


def _cli_mode(args: argparse.Namespace) -> int:
    """Headless CLI mode for batch/scripting use."""
    from pathlib import Path
    from impostor.core.imposition import DuplexMode, ImpositionSettings, impose
    from impostor.utils.logging_setup import setup_logging

    setup_logging(debug=args.debug)

    try:
        mode = DuplexMode[args.duplex.upper()]
    except KeyError:
        print(f"Mode recto-verso invalide: {args.duplex}", file=sys.stderr)
        return 1

    settings = ImpositionSettings(
        input_path=Path(args.input),
        output_path=Path(args.output),
        sheets_per_signature=args.sps,
        duplex_mode=mode,
    )

    result = impose(settings)
    if result.success:
        print(f"Succès : {result.output_path}")
        print(f"   Pages : {result.total_pages}  |  Feuilles : {result.sheets_total}  |  Signatures : {result.num_signatures}")
        return 0
    else:
        print(f"Erreur : {result.error}", file=sys.stderr)
        return 1


def _gui_mode() -> None:
    from impostor.utils.logging_setup import setup_logging
    from impostor.gui.main_window import MainWindow

    setup_logging(debug=False)
    app = MainWindow()
    app.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pdf_impostor",
        description="PDF Impostor – Imposition de PDF pour carnets et agendas",
    )
    sub = parser.add_subparsers(dest="command")

    cli = sub.add_parser("impose", help="Mode ligne de commande (sans interface)")
    cli.add_argument("input",  help="Fichier PDF source")
    cli.add_argument("output", help="Fichier PDF de sortie")
    cli.add_argument("--sps",    type=int, default=0,
                     metavar="N", help="Feuilles par signature (0=magazine)")
    cli.add_argument("--duplex", default="AUTO_DUPLEX",
                     choices=["AUTO_DUPLEX", "MANUAL_COLLATE", "MANUAL_NO_COLLATE"],
                     help="Mode recto-verso")
    cli.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    if args.command == "impose":
        sys.exit(_cli_mode(args))
    else:
        _gui_mode()


if __name__ == "__main__":
    main()
