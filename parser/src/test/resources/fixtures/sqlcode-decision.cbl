       IDENTIFICATION DIVISION.
       PROGRAM-ID. SQLCODEP.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 SQLCODE PIC S9(9) COMP.
       01 WS-CUENTA-ID PIC X(10).
       01 WS-SALDO PIC 9(9)V99.
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC SQL
               SELECT SALDO
               INTO :WS-SALDO
               FROM CUENTAS
               WHERE ID_CUENTA = :WS-CUENTA-ID
           END-EXEC.
           IF SQLCODE = 100
               MOVE 'R900' TO WS-COD-RETORNO
           END-IF.
           GOBACK.
