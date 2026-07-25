       IDENTIFICATION DIVISION.
       PROGRAM-ID. NORMEXPR1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO           PIC 9(7)V99 VALUE 0.
       01 WS-RETURN-CODE     PIC X(4) VALUE SPACES.
       01 WS-ESTADO          PIC X(8) VALUE SPACES.
       01 WS-MONTO           PIC 9(7)V99 VALUE 0.
       01 WS-LIMITE          PIC 9(7)V99 VALUE 0.
       PROCEDURE DIVISION.
       CHECK-SALDO-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-RETURN-CODE
           END-IF.
       LITERAL-INTERNO-PARA.
           IF WS-ESTADO = 'A   B'
               MOVE 1 TO WS-SALDO
           END-IF.
       ESTADO-EVALUATE-PARA.
           EVALUATE WS-ESTADO
               WHEN 'X'
                   MOVE 1 TO WS-SALDO
               WHEN OTHER
                   MOVE 0 TO WS-SALDO
           END-EVALUATE.
       CALCULO-PARA.
           COMPUTE WS-SALDO = WS-MONTO - WS-LIMITE.
