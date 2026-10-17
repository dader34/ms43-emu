-- Runs when the mod is activated: nothing to register on the game engine
-- side; the vehicle side auto-loads from lua/vehicle/extensions/auto/.
if setExtensionUnloadMode and extensions and extensions.load then
  pcall(function()
    setExtensionUnloadMode("ms43dme_ge", "manual")
    extensions.load("ms43dme_ge")
  end)
end
