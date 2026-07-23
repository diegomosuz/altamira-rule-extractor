       IDENTIFICATION DIVISION.
       PROGRAM-ID. COMPREH1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-REGISTRO.
           05 WS-MONTO           PIC 9(7)V99 USAGE COMP-3.
           05 WS-LIMITE          PIC 9(7)V99 VALUE 1000.
           05 WS-CODIGO          PIC X(3).
           05 WS-CONTADOR        PIC 9(4) USAGE COMP.
       01 WS-DESTINO-A            PIC 9(7)V99.
       01 WS-DESTINO-B            PIC 9(7)V99.
       01 WS-DESTINO-C            PIC 9(7)V99.
       01 WS-INDICE               PIC 9(4).
       01 WS-CUENTA-ID             PIC X(10).
       01 WS-SALDO                 PIC 9(9)V99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM VALIDAR-MONTO.
           PERFORM CALCULOS-PARA THRU CALCULOS-PARA-FIN.
           GO TO FIN-PARA.
       VALIDAR-MONTO.
           IF WS-MONTO > WS-LIMITE
               MOVE 'ALT' TO WS-CODIGO
               IF WS-CONTADOR > 0
                   ADD 1 TO WS-CONTADOR
               END-IF
           ELSE
               MOVE 'NOR' TO WS-CODIGO
           END-IF.
           EVALUATE WS-CODIGO
               WHEN 'ALT'
                   MOVE 1 TO WS-CONTADOR
               WHEN 'NOR'
                   MOVE 0 TO WS-CONTADOR
               WHEN OTHER
                   MOVE 9 TO WS-CONTADOR
           END-EVALUATE.
       CALCULOS-PARA.
           MOVE WS-MONTO TO WS-DESTINO-A WS-DESTINO-B WS-DESTINO-C.
           SET WS-INDICE TO 1.
           COMPUTE WS-SALDO = WS-MONTO - WS-LIMITE.
           EXEC SQL
               SELECT SALDO, ESTADO
               INTO :WS-SALDO, :WS-CODIGO
               FROM CUENTAS
               WHERE ID_CUENTA = :WS-CUENTA-ID
           END-EXEC.
           EXEC SQL
               INSERT INTO MOVIMIENTOS (ID_CUENTA, MONTO)
               VALUES (:WS-CUENTA-ID, :WS-MONTO)
           END-EXEC.
           EXEC SQL
               UPDATE CUENTAS
               SET SALDO = :WS-SALDO
               WHERE ID_CUENTA = :WS-CUENTA-ID
           END-EXEC.
           EXEC SQL
               DELETE FROM MOVIMIENTOS
               WHERE ID_CUENTA = :WS-CUENTA-ID
           END-EXEC.
           EXEC SQL
               SELECT A.SALDO
               FROM CUENTAS A, MOVIMIENTOS B
               WHERE A.ID_CUENTA = B.ID_CUENTA
           END-EXEC.
       CALCULOS-PARA-FIN.
           CONTINUE.
       FIN-PARA.
           GOBACK.
