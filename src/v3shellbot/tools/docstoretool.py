import mixedbread as mxbai
import os
from pathlib import Path
import tempfile

from v3shellbot.tools.util import classproperty

class DocStoreTool:
    def __init__(self, store_id: str = None):
        self.mixedbread = mxbai.Mixedbread(api_key=os.getenv("MIXEDBREAD_API_KEY"))
        if store_id is None:
            store_id = os.getenv("MIXEDBREAD_STORE_ID")
            if store_id is None:
                raise ValueError("MIXEDBREAD_STORE_ID is not set")
        self.store_id = store_id
        assert self.store_id, "store-id is required"
        # To get a path to a suitable directory for temporary files (like /tmp), use tempfile.gettempdir()
        self.download_dir = os.getenv("SHELLBOT_DOWNLOAD_DIR", "~/Downloads")

    @property
    def name(self):
        return "document-store"
    
    @classproperty
    def toolname(cls):
        return "document-store"
    
    @property
    def description(self):
        return """This function manages a document store for storing and retrieving documents. It can store most kinds of files including text, PDF, images, and other types. It supports uploading files, downloading files, and semantic searching of file contents, even for images.
        The 'search' operation searches documents and returns a list of high-scoring document chunks, the results include file-id that can be used to download the file if needed.
        The 'upload' operation uploads a file to the document store. It may not be ready immediately for searching.
        The 'download' operation downloads a file from the document store. If the destination_dir is not provided, the file is downloaded to the system temp directory. Either way, the path to the downloaded file is returned.
        """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform: 'search' to search documents, 'download' to download a file, or 'upload' to upload a file",
                    "enum": ["search", "download", "upload"]
                },
                "query": {
                    "type": "string",
                    "description": "Search query for semantic search of document contents. Required when operation is 'search'."
                },
                "file_id": {
                    "type": "string",
                    "description": "The ID of the file to download. Required when operation is 'download'. "
                },
                "destination_dir": {
                    "type": "string",
                    "description": "The destination directory for downloaded files. Optional, defaults to system temp directory. Required when operation is 'download'."
                },
                "file_path": {
                    "type": "string",
                    "description": "The local file path to upload. Required when operation is 'upload'."
                }
            },
            "required": ["operation"]
        }

    def __call__(self, **kwargs):
        op = kwargs.get('operation')
        if not op:
            return f"The function {self.name} requires an 'operation' keyword argument, but didn't get one"
        if op == "search":
            query = kwargs.get('query')
            if not query:
                return f"The function {self.name} with operation {op} requires a 'query' keyword argument, but didn't get one"
            results = self.mixedbread.stores.search(query=query, store_identifiers=[self.store_id], top_k=5)
            return results.data
        elif op == "download":
            file_id = kwargs.get('file_id')
            response = self.mixedbread.files.content(file_id)
            filename = kwargs.get('filename')
            destination_dir = kwargs.get('destination_dir', tempfile.gettempdir())
            if not os.path.exists(destination_dir):
                raise ValueError(f"Destination directory {destination_dir} does not exist")
            destination_path = os.path.join(destination_dir, filename)
            with open(destination_path, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
            return f"File {filename} downloaded to {destination_path}"
        elif op == "upload":
            file_path = kwargs.get('file_path')
            if not os.path.exists(file_path):
                raise ValueError(f"File {file_path} does not exist")
            filename = os.path.basename(file_path)

            # First, create the file in mixedbread
            res = self.mixedbread.stores.files.upload(
                store_identifier=self.store_id,
                file=Path(file_path),
            )
            return f"Uploaded file {filename}, response: {res}"

if __name__ == "__main__":
    docstoretool = DocStoreTool()
    print(docstoretool(operation="upload", file_path="/Users/brendanofallon/Downloads/jcm-14-05110.pdf"))