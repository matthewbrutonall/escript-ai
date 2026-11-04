"""Line-contract marker for Loghi-HTR.

The line workflow is integrated. This module does not call Loghi, open a
network connection, or write a transcription. Recognition, the review gate,
and import stay on the existing external HTR line path. Loghi-HTR is not a
page recognizer. Laypa is not included, and this module does not fall back
to another engine.
"""

LOGHI_INTEGRATED = True
