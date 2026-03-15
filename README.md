# PDF Impostor 🖨️

Logiciel Windows d'imposition PDF pour agendas et carnets.

## Lancer l'application

```bash
python main.py
```

## Mode CLI (sans interface)

```bash
python main.py impose document.pdf document_imposed.pdf --sps 4 --duplex AUTO_DUPLEX
```

Options :
- `--sps N`      Feuilles par signature (0 = magazine complet)
- `--duplex`     `AUTO_DUPLEX` | `MANUAL_COLLATE` | `MANUAL_NO_COLLATE`
- `--debug`      Active les logs détaillés

## Générer le .exe Windows

```bash
pip install pyinstaller
python build.py
# → dist/PDFImpostor/PDFImpostor.exe
```

## Lancer les tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## Concepts d'imposition

### Qu'est-ce qu'une signature ?

Une **signature** est un groupe de feuilles pliées ensemble pour former un cahier.

| Feuilles/sig | Pages/sig | Usage typique           |
|:---:         |:---:       |---                      |
| 0 (magazine) | toutes    | Livrets, mini-comics    |
| 1            | 4          | Test rapide             |
| 4            | 16         | Revues                  |
| 8            | 32         | Livres courants         |

### Algorithme d'imposition

Pour N feuilles → 4N pages (indices 0 à 4N-1) :

```
Feuille 0 recto :  page 4N-1 (droite)  |  page 0 (gauche)
Feuille 0 verso :  page 1    (gauche)  |  page 4N-2 (droite)
Feuille 1 recto :  page 4N-3 (droite)  |  page 2 (gauche)
...
```

---

## Fonctionnalités

### Incluses

- Imposition de PDF pour livres et magazines
- Réordonnancement 2-up des pages pour reliure
- Signatures variables (1 feuille → magazine complet)
- Guide d'utilisation intégré
- Support imprimantes recto-verso auto et manuelle
- Interface graphique Windows moderne (Tkinter)
- Mode CLI pour automatisation/scripts
- Persistance des préférences utilisateur
- Zoom des pages (`zoom` dans `ImpositionSettings`)
- Décalage intérieur (`inside_offset`)
- Compensation du creep (`creep_compensation`)
- Ajustement centrage imprimante (`center_adjustment`)

---
