"""Regression tests for deleting a text region together with its mask."""

import _bootstrap  # noqa: F401

import numpy as np


def test_region_mask_removal_clears_only_the_selected_polygon():
    from editor.mask_region import remove_region_from_mask

    mask = np.full((20, 24), 255, dtype=np.uint8)
    region = {"lines": [[[2, 3], [7, 3], [7, 8], [2, 8]]]}

    result = remove_region_from_mask(mask, region)

    assert result is not None
    assert np.all(result[3:9, 2:8] == 0)
    assert result[2, 2] == 255
    assert result[3, 8] == 255


def test_delete_region_command_restores_region_and_masks_on_undo():
    from editor.commands import DeleteRegionCommand

    class Model:
        def __init__(self):
            self.regions = [{"lines": [[[2, 3], [7, 3], [7, 8], [2, 8]]]}]
            self.raw_mask = np.full((20, 24), 255, dtype=np.uint8)
            self.refined_mask = self.raw_mask.copy()
            self.selection = []

        def remove_region(self, index):
            return self.regions.pop(index)

        def insert_region(self, index, region):
            self.regions.insert(index, region)
            return index

        def set_raw_mask(self, mask):
            self.raw_mask = None if mask is None else np.array(mask, copy=True)

        def set_refined_mask(self, mask):
            self.refined_mask = None if mask is None else np.array(mask, copy=True)

        def set_selection(self, selection):
            self.selection = list(selection)

    model = Model()
    old_raw = model.raw_mask.copy()
    old_refined = model.refined_mask.copy()
    new_raw = old_raw.copy()
    new_refined = old_refined.copy()
    new_raw[3:9, 2:8] = 0
    new_refined[3:9, 2:8] = 0

    command = DeleteRegionCommand(
        model,
        0,
        model.regions[0],
        old_raw_mask=old_raw,
        new_raw_mask=new_raw,
        old_refined_mask=old_refined,
        new_refined_mask=new_refined,
    )
    command.redo()
    assert model.regions == []
    assert np.array_equal(model.raw_mask, new_raw)
    assert np.array_equal(model.refined_mask, new_refined)

    command.undo()
    assert len(model.regions) == 1
    assert np.array_equal(model.raw_mask, old_raw)
    assert np.array_equal(model.refined_mask, old_refined)
    assert model.selection == [0]
