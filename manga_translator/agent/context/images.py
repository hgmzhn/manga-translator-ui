"""Host markers for tool images in append-only conversations."""

EDIT_IMAGE_METADATA = "workspace_edit_images"
ORIGINAL_IMAGE_METADATA = "workspace_original_image"


def prune_images_after_edit(messages):
    """Keep host history intact; ModelImageBudget projects only transport copies."""
