from typing import List, Tuple, Sequence


def pi(A: Sequence[Sequence]) -> Tuple[List, List[int]]:
    """
    π : lista di liste → (lista piatta, lista lunghezze)
    """
    flat: List = []
    lengths: List[int] = []

    for sub in A:
        sub = list(sub)  # accetta anche tuple, generatori, ecc.
        lengths.append(len(sub))
        flat.extend(sub)

    return flat, lengths


def zeta(B: Sequence, lengths: Sequence[int]) -> List[List]:
    """
    ζ : (lista piatta, lista lunghezze) → lista di liste
    """
    B = list(B)
    lengths = list(lengths)

    res: List[List] = []
    idx = 0

    for n in lengths:
        if n < 0:
            raise ValueError("Le lunghezze devono essere non-negative")
        res.append(B[idx : idx + n])
        idx += n

    if idx != len(B):
        raise ValueError("La somma delle lunghezze non coincide con |B|")

    return res
