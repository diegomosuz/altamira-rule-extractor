       IDENTIFICATION DIVISION.
       PROGRAM-ID. EVALBRCH.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 SQLCODE             PIC S9(4) COMP VALUE 0.
       01 WS-ESTADO-OPERACION PIC X(1) VALUE SPACES.
       01 WS-STATUS           PIC X(1) VALUE SPACES.
       01 WS-RESULT           PIC X(1) VALUE SPACES.
       01 WS-SALDO            PIC 9(7)V99 VALUE 0.
       01 WS-LIMITE           PIC 9(7)V99 VALUE 0.
       01 WS-FLAG              PIC X(1) VALUE 'N'.
           88 WS-FLAG-ACTIVO    VALUE 'S'.
       PROCEDURE DIVISION.
       SQLCODE-EVALUATE-PARA.
           EVALUATE SQLCODE
               WHEN 0
                   MOVE 'A' TO WS-ESTADO-OPERACION
               WHEN +100
                   MOVE 'N' TO WS-ESTADO-OPERACION
               WHEN OTHER
                   MOVE 'E' TO WS-ESTADO-OPERACION
           END-EVALUATE.
       STATUS-EVALUATE-PARA.
           EVALUATE WS-STATUS
               WHEN 'A'
                   MOVE 1 TO WS-SALDO
               WHEN OTHER
                   MOVE 0 TO WS-SALDO
           END-EVALUATE.
       NOT-EVALUATE-PARA.
           EVALUATE WS-STATUS
               WHEN NOT 'A'
                   MOVE 1 TO WS-RESULT
           END-EVALUATE.
       TRUE-EVALUATE-PARA.
           EVALUATE TRUE
               WHEN WS-SALDO > WS-LIMITE
                   MOVE 'S' TO WS-RESULT
               WHEN WS-FLAG-ACTIVO
                   MOVE 'A' TO WS-RESULT
               WHEN OTHER
                   MOVE 'N' TO WS-RESULT
           END-EVALUATE.
