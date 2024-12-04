# Sistema Financeiro

Um sistema web para gerenciamento de finanças pessoais desenvolvido com Flask e SQLite.

## Funcionalidades

- Registro de receitas e despesas
- Visualização do saldo atual
- Histórico de transações
- Interface responsiva e moderna
- Armazenamento em banco de dados SQLite

## Requisitos

- Python 3.7+
- Flask
- SQLite3

## Instalação

1. Clone o repositório ou baixe os arquivos
2. Instale as dependências:
```bash
pip install flask
```

3. Execute o aplicativo:
```bash
python app.py
```

4. Acesse o sistema no navegador:
```
http://localhost:5000
```

## Estrutura do Projeto

```
projeto_financeiro/
├── app.py              # Aplicação Flask principal
├── instance/          # Banco de dados SQLite
├── static/
│   ├── css/
│   │   └── style.css  # Estilos personalizados
│   └── js/
│       └── script.js  # JavaScript do cliente
└── templates/
    └── index.html     # Página principal
```

## Uso

1. Acesse a página principal
2. Use o formulário para adicionar novas transações
3. Visualize o resumo financeiro nos cards no topo
4. Consulte o histórico de transações na tabela

## Contribuição

Sinta-se à vontade para contribuir com melhorias através de pull requests.
