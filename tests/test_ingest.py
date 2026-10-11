import random

from ingest.hub import Assembler, chunks
from simulator.clubs import CLUBS
from simulator.engine import simulate


def test_matches_survive_the_stream_interleaved_and_duplicated():
    """Chunks of two matches arrive mixed up, one chunk twice: both come back whole and in order."""
    a = simulate(CLUBS["kestrel"], CLUBS["marlowe"], 1)
    b = simulate(CLUBS["portellis"], CLUBS["ashvale"], 2)
    stream = chunks("a", a) + chunks("b", b)
    stream.append(stream[3])
    random.Random(7).shuffle(stream)
    asm, done = Assembler(), {}
    for msg in stream:
        out = asm.add(msg)
        if out:
            done[out[0]] = out[1]
    assert done["a"] == a and done["b"] == b
