In this project, keys are referred to as layer:row:column, 0 indexed from the top left of that layer. Use the layer display name from the keymap, for example Sym:0:1.
Row 3 has only six keys, 0-5.
On every layer, *:1:0 is shift. *:1:11 is shift too, except where a layer overrides it: on Base it taps apostrophe and holds shift, and on Sym it is ä.

The keyboard is used with a laptop that's using a UK layout. While we may add extra functionality, we need to not confuse the user when the default keyboard is used. 

Instead of blindly following instructions, go for the intent. If there are established ways of doing certain things, suggest alternatives. 

The goal is to rely as little as possible on OS configurations. 

We do not modify generated files. 

Since part of the build is committing generated files, a force push is often necessary. 
