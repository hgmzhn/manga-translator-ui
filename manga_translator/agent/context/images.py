"""Host markers for tool images in append-only conversations."""

EDIT_IMAGE_METADATA = "workspace_edit_images"
ORIGINAL_IMAGE_METADATA = "workspace_original_image"


def prune_images_after_edit(messages):
    """Compatibility no-op: historical images and region snapshots are immutable."""
