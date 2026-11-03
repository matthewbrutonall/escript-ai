"""Line-contract marker for Calamari.

The line workflow is integrated. This module does not call Calamari, open
a network connection, or write a transcription. Recognition, the review
gate, and import stay on the existing external HTR line path. Calamari is
not a page recognizer and this module does not fall back to another engine.
"""

CALAMARI_INTEGRATED = True
