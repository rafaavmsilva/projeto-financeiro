import re
import requests

class CNPJHandler:
    PATTERNS = [
        r'CNPJ[:\s]*(\d{14,15})',  # CNPJ followed by 14 or 15 digits
        r'CNPJ[:\s]*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})',  # CNPJ followed by formatted number
        r'\b(\d{14,15})\b',  # Just 14 or 15 digits
        r'\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b'  # Formatted CNPJ
    ]

    def __init__(self):
        self.cache = {}
        self.failed_cnpjs = set()

    def get_company_info(self, cnpj):
        if cnpj in self.cache:
            return self.cache[cnpj]
        
        try:
            response = requests.get(f'https://brasilapi.com.br/api/cnpj/v1/{cnpj}', timeout=5)
            if response.status_code == 200:
                company_info = response.json()
                self.cache[cnpj] = company_info
                if cnpj in self.failed_cnpjs:
                    self.failed_cnpjs.remove(cnpj)
                return company_info
            else:
                self.failed_cnpjs.add(cnpj)
        except Exception as e:
            print(f"Erro ao buscar informações da empresa: {e}")
            self.failed_cnpjs.add(cnpj)
        return None

    def extract_and_enrich_cnpj(self, description, transaction_type):
        cnpj_match = None
        for pattern in self.PATTERNS:
            match = re.search(pattern, description)
            if match:
                cnpj_match = match
                break

        if not cnpj_match:
            return description

        cnpj = ''.join(filter(str.isdigit, cnpj_match.group(1)))
        if len(cnpj) == 15 and cnpj.startswith('0'):
            cnpj = cnpj[1:]
        elif len(cnpj) != 14:
            return description

        company_info = self.get_company_info(cnpj)
        if company_info:
            razao_social = company_info.get('razao_social', '')
            return description.replace(cnpj_match.group(0), f"{razao_social} (CNPJ: {cnpj})")
        
        return description