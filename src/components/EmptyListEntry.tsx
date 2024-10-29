import {TableRow, TableCell, Typography, TableCellProps} from '@mui/material';

interface EmptyListEntryProps {
  cellProps?: TableCellProps;
}

export const EmptyListEntry: React.FC<EmptyListEntryProps> = ({cellProps}) => {
  return (
    <TableRow>
      <TableCell {...cellProps}>
        <Typography variant="body2" color="grey">
          None
        </Typography>
      </TableCell>
    </TableRow>
  );
};
