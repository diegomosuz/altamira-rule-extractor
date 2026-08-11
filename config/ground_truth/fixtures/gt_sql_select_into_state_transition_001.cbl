       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTSQLST1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CUENTA                PIC X(10).
       01 WS-ESTADO-DB              PIC X(1).
       01 WS-ESTADO-OPERACION      PIC X(1).
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC SQL
               SELECT ESTADO
               INTO :WS-ESTADO-DB
               FROM CUENTAS
               WHERE CUENTA = :WS-CUENTA
           END-EXEC.
           IF WS-ESTADO-DB = 'B'
               MOVE 'R' TO WS-ESTADO-OPERACION
           END-IF.
