"""The name the household sees in the From line.

Lives here rather than in `src/` for the same reason the digest and nudge
wording does: it is English the roommates read, and changing it should not
mean touching code.

It matters more than a cosmetic setting. The sending account is one
person's, so without a display name every digest and every overdue reminder
arrives visibly from that person. The whole point of the design is that a
system assigns and chases so nobody has to be the one who nags. A From line
naming the system rather than the housemate keeps that true.
"""

SENDER_NAME = "Apartment Chores"
