class TransactionHandler:
    TYPE_MAPPING = {
        'PIX RECEBIDO': ['PIX RECEBIDO'],
        'PIX ENVIADO': ['PIX ENVIADO'],
        'TED RECEBIDA': ['TED RECEBIDA', 'TED CREDIT'],
        'TED ENVIADA': ['TED ENVIADA', 'TED DEBIT'],
        'PAGAMENTO': ['PAGAMENTO', 'PGTO', 'PAG'],
        'TARIFA': ['TARIFA', 'TAR'],
        'IOF': ['IOF'],
        'RESGATE': ['RESGATE'],
        'APLICACAO': ['APLICACAO', 'APLICAÇÃO'],
        'COMPRA': ['COMPRA'],
        'COMPENSACAO': ['COMPENSACAO', 'COMPENSAÇÃO'],
        'CHEQUE': ['CHEQUE'],
        'TRANSFERENCIA': ['TRANSFERENCIA', 'TRANSF'],
        'JUROS': ['JUROS'],
        'MULTA': ['MULTA']
    }

    @staticmethod
    def detect_type(description, value):
        description_upper = description.upper()
        
        for tipo, keywords in TransactionHandler.TYPE_MAPPING.items():
            if any(keyword in description_upper for keyword in keywords):
                return tipo

        if 'PIX' in description_upper:
            return 'PIX RECEBIDO' if value > 0 else 'PIX ENVIADO'
        elif 'TED' in description_upper:
            return 'TED RECEBIDA' if value > 0 else 'TED ENVIADA'
        
        return 'CREDITO' if value > 0 else 'DEBITO'