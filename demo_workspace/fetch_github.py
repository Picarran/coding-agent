import urllib.request


def fetch_first_lines(url, n=3):
    """Fetch the given URL and return the first n lines of the response body."""
    with urllib.request.urlopen(url) as response:
        content = response.read().decode("utf-8", errors="replace")
    lines = content.splitlines()
    return lines[:n]


def main():
    url = "https://www.github.com"
    lines = fetch_first_lines(url, 3)
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
