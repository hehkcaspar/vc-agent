import os
import urllib.request
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sample-legal-doc')
os.makedirs(base_dir, exist_ok=True)

nvca_dir = os.path.join(base_dir, 'NVCA')
os.makedirs(nvca_dir, exist_ok=True)

nvca_docs = [
    ("Term_Sheet_2020.docx", "https://nvca.org/wp-content/uploads/2020/07/NVCA-2020-Term-Sheet.docx"),
    ("Certificate_of_Incorporation_2025.docx", "https://nvca.org/wp-content/uploads/2025/10/NVCA-Model-COI-10-1-2025.docx"),
    ("Stock_Purchase_Agreement_2025.docx", "https://nvca.org/wp-content/uploads/2025/10/NVCA-Model-SPA-10-28-2025-1.docx"),
    ("Investors_Rights_Agreement_2025.docx", "https://nvca.org/wp-content/uploads/2025/10/NVCA-Model-IRA-10-1-2025-2-1.docx"),
    ("Voting_Agreement_2025.docx", "https://nvca.org/wp-content/uploads/2024/10/NVCA-Model-VA-10-1-2025.docx"),
    ("ROFR_Co_Sale_Agreement_2026.docx", "https://nvca.org/wp-content/uploads/2026/04/NVCA-Model-ROFRA.docx"),
    ("Management_Rights_Letter.docx", "https://nvca.org/wp-content/uploads/2025/12/NVCA-2020-Management-Rights-Letter-1-1.docx"),
    ("Indemnification_Agreement.docx", "https://nvca.org/wp-content/uploads/2021/12/NVCA-2020-Indemnification-Agreement.docx"),
    ("Model_Legal_Opinion.doc", "https://nvca.org/wp-content/uploads/2019/06/NVCA_Model_Legal-Opinion.doc"),
    ("Life_Science_Early_Stage_Term_Sheet_2023.docx", "https://nvca.org/wp-content/uploads/2023/12/TTO-VC-Simple-Term-Sheet-Dec-2023.docx")
]

opener = urllib.request.build_opener()
opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'),
    ('Accept-Language', 'en-US,en;q=0.9'),
    ('Referer', 'https://nvca.org/model-legal-documents/'),
    ('Cache-Control', 'no-cache'),
    ('Pragma', 'no-cache'),
]
urllib.request.install_opener(opener)

def dl_files(docs, out_dir):
    for name, url in docs:
        out_path = os.path.join(out_dir, name)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            print(f"Skipping {name}, already exists")
            continue
        print(f"Downloading {name} from {url}...")
        try:
            req = urllib.request.Request(url, headers={h[0]: h[1] for h in opener.addheaders})
            with urllib.request.urlopen(req) as response, open(out_path, 'wb') as out_file:
                out_file.write(response.read())
            print("Successfully downloaded.")
        except Exception as e:
            print(f"Error downloading {name}: {e}")

print("Downloading NVCA documents...")
dl_files(nvca_docs, nvca_dir)
