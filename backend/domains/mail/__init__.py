"""Mail helpdesk — per project een eigen mailbox, review-gate.

Inkomende supportmail wordt opgehaald (POP3), spam/automatische rapporten worden
gefilterd, een echte vraag krijgt een NL-concept-antwoord van de LLM in de
merkstem van dat project, en dat concept landt in het Actiecentrum
(status=pending_review). Vincent klikt één keer om te versturen. Geen mail
vertrekt zonder die klik — zelfde discipline als de content-wachtrij.
"""
