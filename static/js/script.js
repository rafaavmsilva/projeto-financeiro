document.addEventListener('DOMContentLoaded', function() {
    const transactionForm = document.getElementById('transactionForm');
    const transactionsTable = document.getElementById('transactionsTable');
    
    // Load initial data
    loadTransactions();
    updateSummary();

    // Sidebar toggle
    const sidebarCollapse = document.getElementById('sidebarCollapse');
    const sidebar = document.getElementById('sidebar');
    
    if (sidebarCollapse) {
        sidebarCollapse.addEventListener('click', function() {
            sidebar.classList.toggle('active');
        });
    }

    // Close sidebar on mobile when clicking outside
    document.addEventListener('click', function(e) {
        if (window.innerWidth <= 768) {
            if (!sidebar.contains(e.target) && !sidebarCollapse.contains(e.target)) {
                sidebar.classList.add('active');
            }
        }
    });

    // Format currency inputs
    const currencyInputs = document.querySelectorAll('input[type="number"][step="0.01"]');
    currencyInputs.forEach(input => {
        input.addEventListener('blur', function(e) {
            const value = parseFloat(e.target.value);
            if (!isNaN(value)) {
                e.target.value = value.toFixed(2);
            }
        });
    });

    // Format dates to Brazilian format
    const formatDate = (dateString) => {
        const date = new Date(dateString);
        return date.toLocaleDateString('pt-BR');
    };

    // Format currency to Brazilian format
    const formatCurrency = (value) => {
        return new Intl.NumberFormat('pt-BR', {
            style: 'currency',
            currency: 'BRL'
        }).format(value);
    };

    transactionForm.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const transaction = {
            type: document.getElementById('type').value,
            description: document.getElementById('description').value,
            value: parseFloat(document.getElementById('value').value),
            date: document.getElementById('date').value
        };

        // Send transaction to the server
        fetch('/api/transactions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(transaction)
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Erro ao adicionar transação: ' + data.error);
            } else {
                loadTransactions();
                updateSummary();
                transactionForm.reset();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Erro ao adicionar transação');
        });
    });

    function loadTransactions() {
        fetch('/api/transactions')
            .then(response => response.json())
            .then(transactions => {
                updateTransactionsTable(transactions);
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Erro ao carregar transações');
            });
    }

    function updateTransactionsTable(transactions) {
        transactionsTable.innerHTML = '';
        
        // Sort transactions by date (most recent first)
        transactions.sort((a, b) => new Date(b.date) - new Date(a.date));

        transactions.forEach(transaction => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${formatDate(transaction.date)}</td>
                <td>${transaction.description}</td>
                <td class="transaction-${transaction.type}">${transaction.type === 'receita' ? 'Receita' : 'Despesa'}</td>
                <td class="transaction-${transaction.type}">${formatCurrency(transaction.value)}</td>
            `;
            transactionsTable.appendChild(row);
        });
    }

    function updateSummary() {
        fetch('/api/summary')
            .then(response => response.json())
            .then(data => {
                document.querySelector('.bg-success .card-text').textContent = `${formatCurrency(data.receitas)}`;
                document.querySelector('.bg-danger .card-text').textContent = `${formatCurrency(data.despesas)}`;
                document.querySelector('.bg-info .card-text').textContent = `${formatCurrency(data.saldo)}`;
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Erro ao atualizar resumo');
            });
    }
});
