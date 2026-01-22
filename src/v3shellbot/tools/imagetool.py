import os
import tempfile
import subprocess
import base64
from typing import Optional

from google import genai
from v3shellbot.tools.util import classproperty


class ImageTool:
    """
    Tool for generating images from text prompts using Google's Gemini image generation API.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the ImageTool.
        
        Args:
            api_key: Google API key. If not provided, will use GEMINI_API_KEY env var.
        """
        if api_key is None:
            api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("Google API key is required. Set GEMINI_API_KEY env var or pass api_key parameter.")
        
        self.client = genai.Client(api_key=api_key)
    
    @property
    def name(self):
        return "image-generator"
    
    @classproperty
    def toolname(cls):
        return "image-generator"
    
    @property
    def description(self):
        return """This function generates an image from a text prompt using Google's Gemini image generation API.
        The function accepts a text prompt describing the image to generate, an optional destination path where
        the image will be saved, and an optional 'open' parameter that, if set to True, will open the image
        using the macOS 'open' command to display it to the user. If no destination path is provided, the image
        is saved to a temporary file.
        The text prompt can be fairly detailed and descriptive, including text and multiple elements to include in the image.
        The function returns the path to the generated image.
        """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate"
                },
                "dest_path": {
                    "type": "string",
                    "description": "Optional file path where the generated image should be saved. If not provided, the image is saved to a temporary file."
                },
                "open": {
                    "type": "boolean",
                    "description": "If True, opens the generated image using the macOS 'open' command to display it to the user. Defaults to False."
                }
            },
            "required": ["prompt"]
        }
    
    def __call__(self, **kwargs):
        prompt = kwargs.get("prompt")
        if not prompt:
            return f"The function {self.name} requires a 'prompt' keyword argument, but didn't get one"
        
        dest_path = kwargs.get("dest_path")
        open_image = kwargs.get("open", False)
        
            # Generate the image
        response = self.client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[prompt],
        )

        for part in response.parts:
            if part.inline_data is not None:
                image_bytes = part.as_image().image_bytes
            
        # Determine the destination path
        if dest_path is None:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
            dest_path = temp_file.name
            temp_file.close()
        
        # Save the image
        with open(dest_path, 'wb') as image_file:
            image_file.write(image_bytes)
        
        # Open the image if requested
        if open_image:
            subprocess.run(['open', dest_path], check=True)
        
        return f"Image generated and saved to: {dest_path}"
            


if __name__ == "__main__":
    tool = ImageTool()
    result = tool(prompt="A futuristic cityscape at sunset with flying cars", open=True)
    print(result)

