import {TableCellProps, TableRow, TableCell, Typography} from '@mui/material';

interface EmptyListEntryProps {
  cellProps?: TableCellProps;
  customText?: string;
}

export const EmptyListEntry: React.FC<EmptyListEntryProps> = ({cellProps, customText}) => {
  return (
    <TableRow>
      <TableCell {...cellProps}>
        <Typography variant="body2" color="grey">
          {customText || 'None'}
        </Typography>
      </TableCell>
    </TableRow>
  );
};
