
import logging
import os
import subprocess
from pathlib import Path

import pyperclip
import pymupdf4llm as pymp4llm
import trafilatura
from tavily import TavilyClient

logger = logging.getLogger(__name__)    

from shellbot2.tools.util import classproperty


class ShellFunction:

    def __init__(self):
        pass

    @property
    def name(self):
        return "shell"

    @classproperty
    def toolname(cls):
        return "shell"

    @property
    def description(self):
        return "This function executes the input as a single command in a standard Zsh linux terminal, and returns the exit code, stdout, and stderr as a string. The input must be a valid Zsh shell command using commonly available linux command line tools."

    @property
    def parameters(self):
        return {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to execute"},
                },
                "required": ['command'],
         }

    def __call__(self, **kwargs):
        cmd = kwargs.get('command')
        if not cmd:
            return f"The function {self.name} was expecting a 'command' keyword argument, but didn't get one"
        logger.info(f"Executing terminal command {cmd}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if len(result.stderr.strip()) > 0:
            return f"Command: {result.args}\nReturn code: {result.returncode}\nStdout: {result.stdout}\nStderr: {result.stderr}"
        else:
            return f"Results from executing terminal command {cmd}: {result.args}\nReturn code: {result.returncode}\nStdout: {result.stdout}"


class ReaderFunction:

    @property
    def name(self):
        return "reader"

    @classproperty
    def toolname(cls):
        return "reader"
    
    @property
    def description(self):
        return "This function extracts text from a local file in pdf or .txt format, or a web url, and returns the text. The input must be a relative or absolute path to a local file, or a web url beginning with http, and the output will be the full text extracted from the file or page."

    @property
    def parameters(self):
        return {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The URL or local file path to extract text from"},
                },
                "required": ['path'],
         }

    def _scrape_site(self, url, max_site_len=25000):
        # Return main text content plus links
        try:
            content = trafilatura.fetch_url(url)
            text = trafilatura.extract(content)
            return text
        except Exception as ex:
            logger.error(f"Error collecting information from url {url}: {ex}")
            return f"Error collecting information from url {url}: {ex}"

    def _extract_from_file(self, path):
        path = os.path.expanduser(path)
        path = Path(path)
        if path.name.endswith(".pdf"):
            md_text = pymp4llm.to_markdown(path)
        else:
            return open(path).read()

    def __call__(self, **kwargs):
        path = kwargs.get('path')
        if not path:
            return f"The function {self.name} was expecting a 'path' argument, but didn't get one"
        logger.info(f"Executing text from {path}")
        if path.startswith("http"):
            text = self._scrape_site(path)
        else:
            text = self._extract_from_file(path)
        return text


class ClipboardFunction:

    @property
    def name(self):
        return "clipboard"

    @classproperty
    def toolname(cls):
        return "clipboard"
    
    @property
    def description(self):
        return "This function provide copy and paste operations using the system clipboard. the 'copy' operation puts text data into the clipboard, and the 'paste' operation reads data from the clipboard."

    @property
    def parameters(self):
        return {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "description": "The copy or paste operation", "enum": ["copy", "paste"]},
                    "data": {"type": "string", "description": "The text to copy to the clipboard"}
                },
                "required": ['operation'],
         }

    def __call__(self, **kwargs):
        op = kwargs.get('operation')
        if not op:
            return "Error running the 'clipboard' function, the required parameter 'operation' was not given"
        if op == "copy":
            data = kwargs.get("data")
            if not data:
                return "Error running the 'clipboard' function, the 'data' parameter is required when the operation is 'copy'"
            pyperclip.copy(data)
            return "Data copied to clipboard successfully"
        elif op == "paste":
            return pyperclip.paste()
        else:
            return f"Unknown operation for clipboard function, the operation must be either 'copy' or 'paste' but found {op}"



class PythonFunction:

    @property
    def name(self):
        return "python"

    @classproperty
    def toolname(cls):
        return "python"
    
    @property
    def description(self):
        return "This function runs python code. It expects a code parameter that is a fully self-contained python script that will be executed on the user's computer. The process exit code, standard output and standard error streams of the program are returned."

    @property
    def parameters(self):
        return {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "A complete python script to run."}
                },
                "required": ['code'],
         }

    def __call__(self, **kwargs):
        python_path = os.getenv("SHELLBOT_PYTHON_PATH", "python")
        op = kwargs.get('code')
        if not op:
            return "Error running the 'python' function, the required parameter 'code' was not given"

        result = subprocess.run([python_path, "-c", op], capture_output=True, text=True, timeout=60)
        result_str = f"Exit code: {result.returncode}\nStdout: {result.stdout}\nStderr: {result.stderr}"
        return result_str


class TavilySearchFunction:

    def __init__(self, api_key=None):
        if api_key:
            self.tavily_client = TavilyClient(api_key=api_key)
        else:
            self.tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    @property
    def name(self):
        return "tavilysearch"
    
    @classproperty
    def toolname(cls):
        return "tavilysearch"
    
    @property
    def description(self):
        return """This function searches the web using Tavily and returns the URLs and short text snippets from the top hits. 
        The query input should be a standard web search query, for instance 'Abraham Lincoln pet mouse' or 'What is the capital of France?'"""
    
    @property
    def parameters(self):
        return {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Web search query"},
                },
                "required": ['query'],
         }


    @staticmethod
    def looks_like_text(text: str) -> bool:
        """
        Determine if a chunk of text looks like normal english text 
        The intent here is to filter out elements that are mostly garbage, like nav elements or elements that are mostly whitespace
        """
        words = text.split()
        lines = text.split("\n")
        word_count = len(words)
        line_count = len(lines)
        # Avg words per line
        avg_words_per_line = word_count / line_count
        if avg_words_per_line < 5:
            return False
        
        # Common words
        common_words = set(["the", "and", "is", "be", "have", "for", "not", "on", "at", "in", "to", "of", "that", "it", "with"])
        text = text.lower()
        # count total occurences of common words
        common_word_count = sum(text.count(word) for word in common_words)
        if common_word_count / word_count < 0.1:
            return False
        
        return True
    
    # def _extract_content(self, url):
    #     try:
    #         logger.info(f"Extracting content from url {url}")
    #         response = self.tavily_client.extract(url, include_images=False, extract_depth='advanced')
    #         result = response['results'][0]
    #         if result:
    #             content = result.get('raw_content', '')
    #             content = scrub_high_entropy_text(content, min_chunk_length=25) 
    #             return "URL: " + result.get('url', '') + "\n Title: " + result.get('title', '') + "\n Content: " + content
    #         else:
    #             return f"No results found for url {url}"
    #     except Exception as ex:
    #         logger.error(f"Error extracting content from url {url}: {ex}")
    #         return f"Error extracting content from url {url}: {ex}"

    def _result_to_text(self, response):
        result = []
        for item in response['results']:
            result.append("URL: " + item.get('url', '') + "\n Title: " + item.get('title', '') + "\n Snippet: " + item.get('content', ''))
        return '\n=====\n'.join(result)

    def _run_query(self, query):
        response = self.tavily_client.search(query, search_depth='advanced', max_results=8, chunks_per_source=3)
        return response
    
    def __call__(self, **kwargs):
        query = kwargs.get('query')
        logger.info(f"Running tavily search for {query}")
        if not query:
            return f"The function {self.name} was expecting a 'query' keyword argument, but didn't get one"
        response = self._run_query(query)
        txt = self._result_to_text(response)
        return txt
    

if __name__=="__main__":    
    f = ReaderFunction()
    resp = f(path="https://www.familycantravel.com/family-friendly-destinations-in-mexico/")
    print(resp)
